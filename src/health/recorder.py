"""Per-worker request recorder behind the /admin/health page.

Wired in by gunicorn.conf.py, which gunicorn loads on its own from the working
directory. It records only when TRAINLOG_STATUS_DIR is set (prod: a tmpfs
volume shared with the trainlog_health sidecar) and the process is not that
sidecar itself (TRAINLOG_STATUS_MONITOR).

Every hook runs inside a production request, so it is stdlib only, does no I/O
on the request path (just dict updates under a lock), and swallows its own
errors: a monitoring bug must never cost a request. A watchdog thread in each
worker mirrors the state to <dir>/worker-<pid>.json every TICK_S seconds and,
for any request running longer than STACK_AFTER_S, captures the stack of the
thread serving it, which is what shows what a stuck worker is waiting on
(a dead analytics host, an outside API, a lock in Postgres) without py-spy.

Each request is also counted under its route pattern (/u/<username>/trips, put
in the environ by src/health/views.py:record_route), never its path, so the
per-route numbers carry no personal data and can be kept for weeks; and every
worker reports the commit it was started from, so a change in timings can be
pinned to a deploy.

The files carry the requests' paths (no query strings), which can name a user,
and only in memory (tmpfs): a path in flight goes when its request ends, a slow
request's after SLOW_KEEP_S, and all of them when the worker exits. The history
src/health/collect.py keeps on disk is aggregate numbers only.
"""

import json
import os
import sys
import threading
import time
import traceback

STATUS_DIR = os.environ.get("TRAINLOG_STATUS_DIR")
ENABLED = bool(STATUS_DIR) and not os.environ.get("TRAINLOG_STATUS_MONITOR")

# Latency histogram upper bounds (ms); one more bucket counts everything above.
HIST_BOUNDS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)
KEEP_MINUTES = 90  # per-minute buckets kept in the worker file
SLOW_MS = 1000  # requests at least this slow are listed individually
SLOW_KEEP = 30  # ...this many per worker
SLOW_KEEP_S = 3600  # ...and for at most this long: paths are personal data
STACK_AFTER_S = 5  # capture the stack of requests running longer than this
TICK_S = 2  # watchdog period: heartbeat, file write, busy-time credit
ROUTE_KEEP_MINUTES = 15  # per-route minute buckets kept (the sidecar saves them within ~1 min)
ROUTE_ENVIRON_KEY = "trainlog.route"
# Per-route counters: [n, sum_ms, max_ms, slow_n, *hist]
R_N, R_SUM, R_MAX, R_SLOW, R_HIST = 0, 1, 2, 3, 4

_lock = threading.Lock()
_state = None
_path = None


def new_bucket():
    return {
        "n": 0,
        "sum_ms": 0.0,
        "max_ms": 0.0,
        "busy_ms": 0.0,
        "err": 0,
        "hist": [0] * (len(HIST_BOUNDS_MS) + 1),
    }


def hist_index(ms):
    for i, bound in enumerate(HIST_BOUNDS_MS):
        if ms <= bound:
            return i
    return len(HIST_BOUNDS_MS)


def new_route_counters():
    return [0, 0.0, 0.0, 0] + [0] * (len(HIST_BOUNDS_MS) + 1)


def add_route_counters(into, other):
    into[R_N] += other[R_N]
    into[R_SUM] += other[R_SUM]
    into[R_MAX] = max(into[R_MAX], other[R_MAX])
    into[R_SLOW] += other[R_SLOW]
    for i in range(len(HIST_BOUNDS_MS) + 1):
        into[R_HIST + i] += other[R_HIST + i]


def git_head(root="."):
    """The commit checked out in `root`, read from .git directly (no git
    binary, no subprocess in a gunicorn hook)."""
    try:
        with open(os.path.join(root, ".git", "HEAD")) as f:
            head = f.read().strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head[5:]
        try:
            with open(os.path.join(root, ".git", ref)) as f:
                return f.read().strip() or None
        except OSError:
            with open(os.path.join(root, ".git", "packed-refs")) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
    except OSError:
        pass
    return None


def minute_of(t):
    return int(t // 60) * 60


def _bucket(minute):
    key = str(minute)
    b = _state["minutes"].get(key)
    if b is None:
        b = _state["minutes"][key] = new_bucket()
    return b


def _credit_busy(entry, until):
    """Credit the time `entry` (a request in flight) has been busy since it was
    last credited, split across the minutes it spans. Done continuously by the
    watchdog, not only at the end, so a minute that a stuck request fills is
    already counted by the time that minute is written to history."""
    t = entry["credited"]
    while t < until:
        m = minute_of(t)
        end = min(until, m + 60)
        _bucket(m)["busy_ms"] += (end - t) * 1000
        t = end
    entry["credited"] = until


def _rss():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _cpu_s():
    t = os.times()
    return t.user + t.system


def _short(filename):
    i = filename.find("/site-packages/")
    if i >= 0:
        return filename[i + len("/site-packages/") :]
    cwd = os.getcwd() + "/"
    return filename[len(cwd) :] if filename.startswith(cwd) else filename


def _stack(frame):
    if frame is None:
        return None
    lines = []
    for fs in traceback.extract_stack(frame)[-30:]:
        line = f"{_short(fs.filename)}:{fs.lineno} in {fs.name}"
        if fs.line:
            line += "\n    " + fs.line.strip()[:160]
        lines.append(line)
    return lines


def _write():
    tmp = _path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_state, f, separators=(",", ":"))
    os.replace(tmp, _path)


def _watchdog():
    while True:
        time.sleep(TICK_S)
        try:
            now = time.time()
            with _lock:
                _state["heartbeat"] = now
                _state["rss"] = _rss()
                _state["cpu_s"] = _cpu_s()
                busy = _state["busy"]
                if busy:
                    frames = None
                    for tid, entry in busy.items():
                        _credit_busy(entry, now)
                        if now - entry["since"] >= STACK_AFTER_S:
                            if frames is None:
                                frames = sys._current_frames()
                            entry["stack"] = _stack(frames.get(int(tid)))
                if _state["slow"] and _state["slow"][0]["t"] < now - SLOW_KEEP_S:
                    _state["slow"] = [x for x in _state["slow"] if x["t"] >= now - SLOW_KEEP_S]
                cutoff = minute_of(now) - KEEP_MINUTES * 60
                for key in [k for k in _state["minutes"] if int(k) < cutoff]:
                    del _state["minutes"][key]
                cutoff = minute_of(now) - ROUTE_KEEP_MINUTES * 60
                for key in [k for k in _state["routes"] if int(k) < cutoff]:
                    del _state["routes"][key]
                _write()
        except Exception:
            pass


# --- gunicorn hooks (see gunicorn.conf.py) -----------------------------------


# Workers that go away leave their file behind, marked "exited", rather than
# deleting it: its last minute or two may not be in the history yet, and
# deleting it left a gap in every chart at each restart. src/health/collect.py
# saves those minutes, keeps exited workers out of the live view, and removes
# the file afterwards.


def mark_exited(path, when=None):
    """Flag a worker file as exited (from any process; the worker is gone or
    going, so nothing else writes it)."""
    try:
        with open(path) as f:
            st = json.load(f)
        if st.get("exited"):
            return
        st["exited"] = when or time.time()
        st["heartbeat"] = min(st.get("heartbeat", st["exited"]), st["exited"])
        st["busy"] = {}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, separators=(",", ":"))
        os.replace(tmp, path)
    except (OSError, ValueError, KeyError):
        pass


def on_starting(server):
    """Master, before the workers: the previous run's workers are gone."""
    if not ENABLED:
        return
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        for name in os.listdir(STATUS_DIR):
            if name.startswith("worker-") and name.endswith(".json"):
                mark_exited(os.path.join(STATUS_DIR, name))
    except Exception:
        pass


def post_worker_init(worker):
    global _state, _path
    if not ENABLED:
        return
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        pid = os.getpid()
        now = time.time()
        _path = os.path.join(STATUS_DIR, f"worker-{pid}.json")
        _state = {
            "pid": pid,
            "threads": max(1, int(getattr(worker.cfg, "threads", 1) or 1)),
            # What this worker's code was loaded from: a later git pull without a
            # restart changes the checkout, not what runs.
            "commit": git_head(),
            "booted": now,
            "heartbeat": now,
            "served": 0,
            "rss": _rss(),
            "cpu_s": _cpu_s(),
            "busy": {},  # thread id -> request in flight
            "minutes": {},  # minute epoch -> bucket
            "routes": {},  # minute epoch -> {"GET /route/<pattern>": counters}
            "slow": [],
        }
        with _lock:
            _write()
        threading.Thread(target=_watchdog, name="health-watchdog", daemon=True).start()
    except Exception:
        _state = None


def pre_request(worker, req):
    if _state is None:
        return
    try:
        now = time.time()
        with _lock:
            _state["busy"][str(threading.get_ident())] = {
                "method": req.method,
                "path": (req.path or "")[:200],
                "since": now,
                "credited": now,
            }
    except Exception:
        pass


def post_request(worker, req, environ, resp):
    if _state is None:
        return
    try:
        now = time.time()
        with _lock:
            entry = _state["busy"].pop(str(threading.get_ident()), None)
            if entry is None:
                return
            _credit_busy(entry, now)
            ms = (now - entry["since"]) * 1000
            status = getattr(resp, "status_code", None)
            b = _bucket(minute_of(now))
            b["n"] += 1
            b["sum_ms"] += ms
            b["max_ms"] = max(b["max_ms"], ms)
            b["hist"][hist_index(ms)] += 1
            if isinstance(status, int) and status >= 500:
                b["err"] += 1
            rule = environ.get(ROUTE_ENVIRON_KEY) if environ else None
            key = f"{entry['method']} {rule or '(no route)'}"
            routes = _state["routes"].setdefault(str(minute_of(now)), {})
            rc = routes.get(key)
            if rc is None:
                rc = routes[key] = new_route_counters()
            rc[R_N] += 1
            rc[R_SUM] += ms
            rc[R_MAX] = max(rc[R_MAX], ms)
            if ms >= SLOW_MS:
                rc[R_SLOW] += 1
            rc[R_HIST + hist_index(ms)] += 1
            if ms >= SLOW_MS:
                _state["slow"].append(
                    {
                        "t": now,
                        "method": entry["method"],
                        "path": entry["path"],
                        "ms": round(ms),
                        "status": status,
                    }
                )
                del _state["slow"][:-SLOW_KEEP]
            _state["served"] += 1
    except Exception:
        pass


def worker_exit(server, worker):
    """Worker, on a clean exit: last write, flagged exited."""
    if _state is None or not _path:
        return
    try:
        now = time.time()
        with _lock:
            _state["heartbeat"] = now
            _state["exited"] = now
            _state["busy"] = {}
            _write()
    except Exception:
        pass


def child_exit(server, worker):
    """Master, for any worker that went away, including one killed for a
    timeout or by the OOM killer, which never runs worker_exit."""
    if not ENABLED:
        return
    mark_exited(os.path.join(STATUS_DIR, f"worker-{worker.pid}.json"))
