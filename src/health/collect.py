"""Turns what the gunicorn workers record (src/health/recorder.py) into the
/admin/health page's data: the live worker table, recent latency, host and
Postgres state, and a per-minute history kept on disk.

Runs in the trainlog_health sidecar (its own gunicorn, so the page still loads
when every site worker is stuck), or in the site itself as a fallback. Stdlib +
psycopg2 only, and nothing here imports the app.

History: one JSON line per minute in HISTORY_DIR/YYYY-MM-DD.jsonl (UTC days),
HISTORY_DAYS kept. Aggregate numbers only, no paths or users. Per-route counters
go to YYYY-MM-DD.routes.jsonl, one line per ROUTE_BUCKET_S and commit, keyed by
route pattern (no paths either); the bucket being filled lives in
routes_open.json. deploys.jsonl lists each commit the workers started from. Written by
sample(), which the sidecar calls every SAMPLE_EVERY_S whether or not anyone
is looking; in the fallback it only runs while the page is open.
"""

import datetime
import fcntl
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time

from src.health import recorder as rec

logger = logging.getLogger(__name__)

HISTORY_DIR = os.environ.get("TRAINLOG_HEALTH_HISTORY", "databases/health")
HISTORY_DAYS = 30
STALE_S = 15  # no heartbeat for this long: worker shown as unresponsive
FORGET_S = 600  # ...and for this long: its file is removed
EXITED_KEEP_S = 900  # an exited worker's file is kept this long, for its last minutes to be saved
PERSIST_GRACE_S = 10  # a minute is final this long after it ends
SAMPLE_EVERY_S = 10
CACHE_S = 4  # sample() result reused for this long
RECENT_MINUTES = 5
SLOW_WINDOW_S = rec.SLOW_KEEP_S
MAX_POINTS = 360

ROUTE_BUCKET_S = 600
ROUTE_ROWS = 40
# Per-minute route counters too (YYYY-MM-DD.routes1m.jsonl), for the detail a
# zoomed route chart wants; kept this long, the 10-minute buckets for HISTORY_DAYS.
ROUTE_MINUTE_DAYS = 3

RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def is_monitor():
    return bool(os.environ.get("TRAINLOG_STATUS_MONITOR"))


# --- worker files -------------------------------------------------------------


def read_states(now):
    states = []
    d = rec.STATUS_DIR
    if not d or not os.path.isdir(d):
        return states
    for name in os.listdir(d):
        if not (name.startswith("worker-") and name.endswith(".json")):
            continue
        path = os.path.join(d, name)
        try:
            with open(path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        age = now - st.get("heartbeat", 0)
        if age > (EXITED_KEEP_S if st.get("exited") else FORGET_S):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        st["_stale"] = age > STALE_S and not st.get("exited")
        states.append(st)
    states.sort(key=lambda s: s.get("booted", 0))
    return states


def merge(states, minutes):
    agg = rec.new_bucket()
    for st in states:
        buckets = st.get("minutes", {})
        for m in minutes:
            b = buckets.get(str(m))
            if not b:
                continue
            agg["n"] += b["n"]
            agg["sum_ms"] += b["sum_ms"]
            agg["busy_ms"] += b["busy_ms"]
            agg["err"] += b["err"]
            agg["max_ms"] = max(agg["max_ms"], b["max_ms"])
            agg["hist"] = [a + c for a, c in zip(agg["hist"], b["hist"])]
    return agg


def capacity(states, m):
    """Request slots (workers x threads) in minute m, each weighted by the part
    of the minute it was up: across a restart the outgoing and incoming
    workers share the minute instead of doubling it."""
    total = 0.0
    for st in states:
        up = min(st.get("heartbeat", 0), m + 60) - max(st.get("booted", 0), m)
        if up > 0:
            total += st.get("threads", 1) * min(up, 60) / 60
    return round(total, 2)


def percentile(hist, q, max_ms):
    n = sum(hist)
    if not n:
        return None
    target = q * n
    cum = 0
    lo = 0.0
    bounds = rec.HIST_BOUNDS_MS
    for i, c in enumerate(hist):
        hi = bounds[i] if i < len(bounds) else None
        if c and cum + c >= target:
            if hi is None:
                return round(max_ms)
            return round(min(lo + (target - cum) / c * (hi - lo), max_ms or hi))
        cum += c
        if hi is not None:
            lo = hi
    return round(max_ms)


# psycopg2 interpolates parameters client-side, so pg_stat_activity holds the
# statement with its values in it (emails, names, tokens). Only the shape is
# shown: string and number literals become ?. Redacted before truncating, so a
# string cut in half can't slip through unterminated.
_SQL_STR_RE = re.compile(r"(?:\b[EeBbXxUu]&?)?'(?:[^']|'')*'")
_SQL_OPEN_STR_RE = re.compile(r"'[^']*$")
_SQL_DOLLAR_RE = re.compile(r"\$([A-Za-z_]*)\$.*?\$\1\$", re.S)
_SQL_NUM_RE = re.compile(r"(?<![\w.$\"])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w\"])")
_SQL_WS_RE = re.compile(r"\s+")


def redact_sql(query, limit=160):
    if not query:
        return query
    q = _SQL_DOLLAR_RE.sub("?", query)
    q = _SQL_STR_RE.sub("?", q)
    q = _SQL_OPEN_STR_RE.sub("?", q)
    q = _SQL_NUM_RE.sub("?", q)
    q = _SQL_WS_RE.sub(" ", q).strip()
    return q if len(q) <= limit else q[: limit - 1] + "…"


_USER_RE = re.compile(r"/u/[^/]+")
_ID_RE = re.compile(r"/(\d+|[0-9a-f]{16,}|[0-9a-f-]{36})(?=/|$)")


# Routes whose tail is free-form (app.py: /forwardRouting/<routingType>/<path:path>,
# the router's own URL with the trip's coordinates in it): keep the type, star the rest.
_TAIL_RE = re.compile(r"^(/forwardRouting/[^/]+)/.*")


def route_of(path):
    """Group paths the way routes are: /u/<user>/trip/123 -> /u/*/trip/#,
    /forwardRouting/bus/route/v1/driving/6.1,49.1;... -> /forwardRouting/bus/*"""
    path = _TAIL_RE.sub(r"\1/*", path)
    return _ID_RE.sub("/#", _USER_RE.sub("/u/*", path))


_cpu_prev = {}  # pid -> (heartbeat, cpu_s, last pct)


def live(now, states):
    """Exited workers (kept until their last minutes are saved) count in the
    recent numbers and slow list, not in the worker table or capacity."""
    all_states, states = states, [st for st in states if not st.get("exited")]
    workers = []
    busy = 0
    oldest = 0.0
    for st in states:
        requests = []
        for entry in (st.get("busy") or {}).values():
            age = now - entry["since"]
            oldest = max(oldest, age)
            requests.append(
                {
                    "method": entry["method"],
                    "path": entry["path"],
                    "for_s": round(age, 1),
                    "stack": entry.get("stack"),
                }
            )
        requests.sort(key=lambda r: -r["for_s"])
        busy += len(requests)

        pid = st["pid"]
        hb, cpu = st.get("heartbeat", 0), st.get("cpu_s", 0)
        prev = _cpu_prev.get(pid)
        pct = prev[2] if prev else None
        if prev and hb > prev[0]:
            pct = round(100 * (cpu - prev[1]) / (hb - prev[0]), 1)
        if not prev or hb > prev[0]:
            _cpu_prev[pid] = (hb, cpu, pct)

        state = "stale" if st["_stale"] else ("busy" if requests else "idle")
        workers.append(
            {
                "pid": pid,
                "state": state,
                "threads": st.get("threads", 1),
                "uptime_s": round(now - st.get("booted", now)),
                "heartbeat_s": round(now - hb, 1),
                "served": st.get("served", 0),
                "rss_mb": round(st["rss"] / 2**20) if st.get("rss") else None,
                "cpu_pct": pct,
                "requests": requests,
            }
        )
    for pid in set(_cpu_prev) - {st["pid"] for st in states}:
        del _cpu_prev[pid]

    cur = rec.minute_of(now)
    first = cur - (RECENT_MINUTES - 1) * 60
    b = merge(all_states, range(first, cur + 60, 60))
    span_min = max((now - first) / 60, 1 / 60)
    recent = {
        "minutes": RECENT_MINUTES,
        "req_per_min": round(b["n"] / span_min, 1),
        "p50": percentile(b["hist"], 0.5, b["max_ms"]),
        "p95": percentile(b["hist"], 0.95, b["max_ms"]),
        "max": round(b["max_ms"]) if b["n"] else None,
        "err": b["err"],
        "util_pct": None,
    }
    cap = sum(st.get("threads", 1) for st in states)
    if cap:
        recent["util_pct"] = round(100 * b["busy_ms"] / (cap * span_min * 60000), 1)

    slow = [s for st in all_states for s in st.get("slow", []) if now - s["t"] <= SLOW_WINDOW_S]
    slow.sort(key=lambda s: -s["t"])
    by_route = {}
    for s in slow:
        r = by_route.setdefault(route_of(s["path"]), {"route": route_of(s["path"]), "n": 0, "max": 0, "sum": 0})
        r["n"] += 1
        r["sum"] += s["ms"]
        r["max"] = max(r["max"], s["ms"])
    routes = sorted(by_route.values(), key=lambda r: (-r["n"], -r["max"]))[:10]
    for r in routes:
        r["avg"] = round(r.pop("sum") / r["n"])

    return {
        "workers": workers,
        "totals": {
            "workers": len(states),
            "capacity": cap,
            "busy": busy,
            "stale": sum(1 for w in workers if w["state"] == "stale"),
            "oldest_s": round(oldest, 1),
        },
        "recent": recent,
        "slow": slow[:25],
        "slow_routes": routes,
    }


# --- host -------------------------------------------------------------------

_stat_prev = None


def host():
    global _stat_prev
    out = {"cpus": os.cpu_count()}
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        pass
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        total, avail = info["MemTotal"], info["MemAvailable"]
        out["mem_total_gb"] = round(total / 2**30, 1)
        out["mem_used_pct"] = round(100 * (total - avail) / total, 1)
        if info.get("SwapTotal"):
            out["swap_used_pct"] = round(100 * (info["SwapTotal"] - info["SwapFree"]) / info["SwapTotal"], 1)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    try:
        with open("/proc/stat") as f:
            vals = [int(x) for x in f.readline().split()[1:9]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        if _stat_prev and total > _stat_prev[1]:
            dt = total - _stat_prev[1]
            out["cpu_pct"] = round(100 * (dt - (idle - _stat_prev[0])) / dt, 1)
            out["iowait_pct"] = round(100 * (vals[4] - _stat_prev[2]) / dt, 1)
        _stat_prev = (idle, total, vals[4])
    except (OSError, ValueError, IndexError):
        pass
    try:
        du = shutil.disk_usage(".")
        out["disk_used_pct"] = round(100 * du.used / du.total, 1)
        out["disk_free_gb"] = round(du.free / 2**30, 1)
    except OSError:
        pass
    return out


# --- Postgres ---------------------------------------------------------------


def pg():
    """Connection counts and the longest-running statements. Own short-lived
    connection with tight timeouts, never the app's pool: the page has to load
    when the database is the problem, and then say so."""
    if not os.environ.get("POSTGRES_HOST"):
        return {"ok": False, "error": "not configured"}
    t0 = time.time()
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ["POSTGRES_HOST"],
            port=os.environ.get("POSTGRES_PORT", 5432),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            connect_timeout=2,
            options="-c statement_timeout=2000",
            application_name="trainlog-health",
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*),
                           count(*) FILTER (WHERE state = 'active'),
                           count(*) FILTER (WHERE state = 'idle in transaction'),
                           count(*) FILTER (WHERE wait_event_type = 'Lock'),
                           current_setting('max_connections')::int
                    FROM pg_stat_activity
                    WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
                    """
                )
                total, active, idle_tx, waiting, max_conn = cur.fetchone()
                cur.execute(
                    """
                    SELECT pid, state, wait_event_type, wait_event,
                           extract(epoch FROM now() - coalesce(xact_start, query_start)),
                           left(query, 4000)
                    FROM pg_stat_activity
                    WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
                      AND state <> 'idle'
                    ORDER BY coalesce(xact_start, query_start)
                    LIMIT 8
                    """
                )
                queries = [
                    {
                        "pid": r[0],
                        "state": r[1],
                        "wait": f"{r[2]}:{r[3]}" if r[2] else None,
                        "age_s": round(float(r[4] or 0), 1),
                        "query": redact_sql(r[5]),
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
        return {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000),
            "connections": total,
            "active": active,
            "idle_in_tx": idle_tx,
            "waiting": waiting,
            "max_connections": max_conn,
            "queries": queries,
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": round((time.time() - t0) * 1000),
            "error": f"{type(e).__name__}: {str(e).strip()[:200]}",
        }


# --- history ----------------------------------------------------------------

_peaks = {}  # minute -> most busy slots seen by a sample in it


def _day(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()


def _day_file(t, kind=""):
    return os.path.join(HISTORY_DIR, f"{_day(t).isoformat()}{kind}.jsonl")


def _days(since, until):
    day, last = _day(since), _day(until)
    while day <= last:
        yield day
        day += datetime.timedelta(days=1)


def _merge_routes(states, m, open_buckets):
    """Add minute m of every worker's per-route counters to the open bucket of
    its commit."""
    t = m // ROUTE_BUCKET_S * ROUTE_BUCKET_S
    for st in states:
        minute = st.get("routes", {}).get(str(m))
        if not minute:
            continue
        commit = st.get("commit") or "unknown"
        bucket = open_buckets.setdefault(f"{t}|{commit}", {"t": t, "c": commit, "r": {}})
        for key, counters in minute.items():
            cur = bucket["r"].get(key)
            if cur is None:
                bucket["r"][key] = list(counters)
            else:
                rec.add_route_counters(cur, counters)


def _minute_routes(states, m):
    """Minute m's per-route counters of all workers, per commit (fresh copies)."""
    out = {}
    for st in states:
        minute = st.get("routes", {}).get(str(m))
        if not minute:
            continue
        per = out.setdefault(st.get("commit") or "unknown", {})
        for key, counters in minute.items():
            cur = per.get(key)
            if cur is None:
                per[key] = list(counters)
            else:
                rec.add_route_counters(cur, counters)
    for per in out.values():
        for counters in per.values():
            counters[rec.R_SUM] = round(counters[rec.R_SUM])
            counters[rec.R_MAX] = round(counters[rec.R_MAX])
    return out


def _record_deploy(states):
    """Append to deploys.jsonl when the workers run a commit other than the last
    one recorded: the newest commit, at the time its first worker booted."""
    booted = {}
    for st in states:
        c = st.get("commit")
        if c:
            booted[c] = min(booted.get(c, st.get("booted", 0)), st.get("booted", 0))
    if not booted:
        return
    newest = max(booted, key=booted.get)
    path = os.path.join(HISTORY_DIR, "deploys.jsonl")
    last = None
    try:
        with open(path) as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)
    except (OSError, ValueError):
        pass
    if last and last.get("c") == newest:
        return
    with open(path, "a") as f:
        f.write(json.dumps({"t": round(booted[newest]), "c": newest, "prev": last.get("c") if last else None}) + "\n")


def persist(now, states, host_now, pg_now):
    """Append every minute that is now final and not yet written. Idempotent
    and safe across processes (flock + last_minute marker)."""
    if not states:
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(HISTORY_DIR, ".lock"), "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        marker = os.path.join(HISTORY_DIR, "last_minute")
        try:
            with open(marker) as f:
                last = int(f.read().strip())
        except (OSError, ValueError):
            last = None
        upto = rec.minute_of(now - PERSIST_GRACE_S) - 60
        known = [int(k) for st in states for k in st.get("minutes", {})]
        start = min(known) if known else upto
        if last is not None:
            start = max(start, last + 60)
        start = max(start, upto - rec.KEEP_MINUTES * 60)
        if start > upto:
            return

        open_path = os.path.join(HISTORY_DIR, "routes_open.json")
        try:
            with open(open_path) as f:
                open_buckets = json.load(f)
        except (OSError, ValueError):
            open_buckets = {}

        lines = {}
        for m in range(start, upto + 60, 60):
            _merge_routes(states, m, open_buckets)
            for commit, routes_m in _minute_routes(states, m).items():
                lines.setdefault(_day_file(m, ".routes1m"), []).append(
                    json.dumps({"t": m, "c": commit, "r": routes_m}, separators=(",", ":"))
                )
            cap = capacity(states, m)
            if not cap:
                continue
            b = merge(states, [m])
            row = {
                "t": m,
                "cap": cap,
                "n": b["n"],
                "err": b["err"],
                "busy": round(b["busy_ms"] / 60000, 3),
                "sum": round(b["sum_ms"]),
                "max": round(b["max_ms"]),
                "hist": b["hist"],
            }
            if m in _peaks:
                row["peak"] = _peaks[m]
            if m == upto:  # the sampled values describe "now", i.e. this minute
                if "load" in host_now:
                    row["load1"] = host_now["load"][0]
                for k in ("cpu_pct", "mem_used_pct", "iowait_pct"):
                    if k in host_now:
                        row[k] = host_now[k]
                if pg_now.get("ok"):
                    row["pg_ms"] = pg_now["latency_ms"]
                    row["pg_active"] = pg_now["active"]
                    row["pg_waiting"] = pg_now["waiting"]
                elif pg_now.get("error") != "not configured":
                    row["pg_down"] = 1
            lines.setdefault(_day_file(m), []).append(json.dumps(row, separators=(",", ":")))
        # Route buckets whose minutes are all final move to the day file.
        for key in sorted(open_buckets, key=lambda k: int(k.split("|")[0])):
            bucket = open_buckets[key]
            if bucket["t"] + ROUTE_BUCKET_S > upto + 60:
                continue
            del open_buckets[key]
            for counters in bucket["r"].values():
                counters[rec.R_SUM] = round(counters[rec.R_SUM])
                counters[rec.R_MAX] = round(counters[rec.R_MAX])
            lines.setdefault(_day_file(bucket["t"], ".routes"), []).append(
                json.dumps(bucket, separators=(",", ":"))
            )
        for path, rows in lines.items():
            with open(path, "a") as f:
                f.write("\n".join(rows) + "\n")
        tmp = open_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(open_buckets, f, separators=(",", ":"))
        os.replace(tmp, open_path)
        _record_deploy(states)
        tmp = marker + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(upto))
        os.replace(tmp, marker)

        today = datetime.datetime.now(datetime.timezone.utc).date()
        cutoff = (today - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
        cutoff_1m = (today - datetime.timedelta(days=ROUTE_MINUTE_DAYS)).isoformat()
        for name in os.listdir(HISTORY_DIR):
            if name.endswith(".jsonl") and name[:10] < (cutoff_1m if ".routes1m." in name else cutoff):
                try:
                    os.remove(os.path.join(HISTORY_DIR, name))
                except OSError:
                    pass


def _window(range_key, frm=None, to=None):
    """(since, until): the range up to now, or an explicit from/to (epoch s, a
    zoomed chart asking for its visible part), clamped to what is kept."""
    now = time.time()
    if frm is not None and to is not None:
        until = min(float(to), now)
        since = max(float(frm), now - RANGES["30d"])
        return min(since, until - 120), until
    return now - RANGES.get(range_key, RANGES["1h"]), now


def history(range_key, max_points=MAX_POINTS, frm=None, to=None):
    max_points = min(max(int(max_points or MAX_POINTS), 60), 1500)
    since, now = _window(range_key, frm, to)
    since = rec.minute_of(since)
    rows = []
    day = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).date()
    last_day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    while day <= last_day:
        path = os.path.join(HISTORY_DIR, f"{day.isoformat()}.jsonl")
        try:
            with open(path) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("t", 0) >= since:
                        rows.append(row)
        except OSError:
            pass
        day += datetime.timedelta(days=1)

    # Point spacing from the span the data covers, not the range picked: a few
    # hours of history on the 7d view would otherwise be drawn in 7-minute steps.
    if rows:
        since = max(since, min(r["t"] for r in rows))
    step = max(1, math.ceil((now - since) / 60 / max_points)) * 60
    groups = {}
    for row in rows:
        groups.setdefault(row["t"] // step * step, []).append(row)

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def most(vals):
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    points = []
    t = int(since // step * step)
    while t <= now:
        g = groups.get(t)
        if not g:
            points.append({"t": t})
        else:
            hist = [sum(col) for col in zip(*(r["hist"] for r in g))]
            mx = max(r["max"] for r in g)
            n = sum(r["n"] for r in g)
            points.append(
                {
                    "t": t,
                    "cap": max(r["cap"] for r in g),
                    "busy": mean([r["busy"] for r in g]),
                    "peak": most([r.get("peak") for r in g]),
                    "req_per_min": round(n / len(g), 1),
                    "err": sum(r["err"] for r in g),
                    "p50": percentile(hist, 0.5, mx),
                    "p95": percentile(hist, 0.95, mx),
                    "max": mx if n else None,
                    "load1": mean([r.get("load1") for r in g]),
                    "cpu_pct": mean([r.get("cpu_pct") for r in g]),
                    "mem_used_pct": mean([r.get("mem_used_pct") for r in g]),
                    "iowait_pct": mean([r.get("iowait_pct") for r in g]),
                    "pg_ms": most([r.get("pg_ms") for r in g]),
                    "pg_waiting": most([r.get("pg_waiting") for r in g]),
                    "pg_down": most([r.get("pg_down") for r in g]),
                }
            )
        t += step
    return {
        "range": range_key if range_key in RANGES else "1h",
        "step_s": step,
        "points": points,
        "from": since,
        "to": now,
        "deploys": [d for d in deploys() if since <= d["t"] <= now],
    }


# --- deploys and routes -----------------------------------------------------

_commit_info = {}


def commit_info(sha):
    """Subject and tag of a commit, from the checkout (cached; git is in the image)."""
    if sha in _commit_info:
        return _commit_info[sha]
    info = {"subject": None, "tag": None}
    if sha and sha != "unknown":
        try:
            out = subprocess.run(
                ["git", "log", "-1", "--format=%s", sha], capture_output=True, text=True, timeout=3
            )
            if out.returncode == 0:
                info["subject"] = out.stdout.strip()[:140]
            out = subprocess.run(
                ["git", "describe", "--tags", "--exact-match", sha], capture_output=True, text=True, timeout=3
            )
            if out.returncode == 0:
                info["tag"] = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    _commit_info[sha] = info
    return info


def deploys():
    out = []
    try:
        with open(os.path.join(HISTORY_DIR, "deploys.jsonl")) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                d.update(commit_info(d["c"]))
                d["short"] = (d["c"] or "?")[:7]
                out.append(d)
    except OSError:
        pass
    return out


def _route_rows(since, until, minute=False):
    rows = []
    kind = ".routes1m" if minute else ".routes"
    for day in _days(since, until):
        try:
            with open(os.path.join(HISTORY_DIR, f"{day.isoformat()}{kind}.jsonl")) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if since <= row["t"] < until:
                        rows.append(row)
        except OSError:
            pass
    if minute:
        return rows
    try:
        with open(os.path.join(HISTORY_DIR, "routes_open.json")) as f:
            rows += [b for b in json.load(f).values() if since <= b["t"] < until]
    except (OSError, ValueError):
        pass
    return rows


def _sum_routes(rows, commit=None):
    out = {}
    for row in rows:
        if commit is not None and row["c"] != commit:
            continue
        for key, counters in row["r"].items():
            cur = out.get(key)
            if cur is None:
                out[key] = list(counters)
            else:
                rec.add_route_counters(cur, counters)
    return out


def _route_stats(c, total_ms):
    n = c[rec.R_N]
    return {
        "n": n,
        "time_s": round(c[rec.R_SUM] / 1000),
        "share": round(100 * c[rec.R_SUM] / total_ms, 1) if total_ms else None,
        "avg": round(c[rec.R_SUM] / n) if n else None,
        "p95": percentile(c[rec.R_HIST :], 0.95, c[rec.R_MAX]),
        "max": round(c[rec.R_MAX]),
        "slow": c[rec.R_SLOW],
    }


def route_series(range_key, key, max_points=300, frm=None, to=None):
    """One route over the window: a time series (requests per minute, avg, p95,
    max, slow) and its numbers per commit, oldest first, so a deploy's effect on
    that route reads straight off both. Per-minute points where the minute lines
    reach (the last ROUTE_MINUTE_DAYS) and the window is short enough, 10-minute
    buckets before that; each point carries its own span ("s")."""
    max_points = max(int(max_points or 300), 30)
    since, now = _window(range_key, frm, to)
    span = now - since
    fine = span / 60 / max_points < ROUTE_BUCKET_S / 60
    minute_rows = _route_rows(since - 60, now + 1, minute=True) if fine else []
    coverage = min((row["t"] for row in minute_rows), default=math.inf)
    rows_1m = [row for row in minute_rows if key in row["r"]]
    rows_10m = [
        row for row in _route_rows(since - ROUTE_BUCKET_S, min(now, coverage) + 1)
        if key in row["r"] and row["t"] + ROUTE_BUCKET_S <= coverage
    ]
    first = min((row["t"] for row in rows_1m + rows_10m), default=None)
    if first is not None:  # spacing from the span with data, as in history()
        since = max(since, first)
        span = now - since
    step_10m = ROUTE_BUCKET_S * max(1, math.ceil(span / ROUTE_BUCKET_S / max_points))
    step_1m = 60 * max(1, math.ceil(span / 60 / max_points))

    commits = {}
    def series(rows, step, start, end):
        groups = {}
        for row in rows:
            counters = row["r"][key]
            rec.add_route_counters(groups.setdefault(row["t"] // step * step, rec.new_route_counters()), counters)
            c = commits.setdefault(row["c"], {"first": row["t"], "last": row["t"], "counters": rec.new_route_counters()})
            c["first"], c["last"] = min(c["first"], row["t"]), max(c["last"], row["t"])
            rec.add_route_counters(c["counters"], counters)
        out = []
        t = int(start // step * step)
        while t < end:
            g = groups.get(t)
            if g is None:
                out.append({"t": t, "s": step})
            else:
                stats = _route_stats(g, 0)
                stats.pop("share", None)
                stats["req_per_min"] = round(g[rec.R_N] / (step / 60), 2)
                out.append({"t": t, "s": step, **stats})
            t += step
        return out

    split = min(max(coverage, since), now) if fine else now
    points = series(rows_10m, step_10m, since, split) if since < split else []
    if fine and split < now:
        points += series(rows_1m, step_1m, split, now + 1)
    step = step_1m if fine and split < now else step_10m

    known = {d["c"]: d for d in deploys()}
    per_commit = []
    for sha, c in sorted(commits.items(), key=lambda kv: kv[1]["first"]):
        stats = _route_stats(c["counters"], 0)
        stats.pop("share", None)
        info = commit_info(sha)
        per_commit.append({
            "c": sha, "short": (sha or "?")[:7], "subject": info["subject"], "tag": info["tag"],
            "deployed": known.get(sha, {}).get("t"), "first": c["first"], "last": c["last"] + ROUTE_BUCKET_S, **stats,
        })
    method, _, route = key.partition(" ")
    return {
        "range": range_key if range_key in RANGES else "1h",
        "key": key, "method": method, "route": route, "step_s": step,
        "from": since, "to": now,
        "points": points,
        "commits": per_commit,
        "deploys": [d for d in deploys() if since <= d["t"] <= now],
    }


def routes(range_key, compare="prev"):
    """Per-route totals over the range, busiest (by total time) first, each
    against a baseline: the previous period of the same length ("prev"), or
    the same length either side of a deploy (compare = its commit), split by
    the commit each request ran on rather than by the clock."""
    span = RANGES.get(range_key, RANGES["1h"])
    now = time.time()
    since = now - span
    base, info = None, None
    if compare == "prev":
        cur = _sum_routes(_route_rows(since, now + 1))
        base = _sum_routes(_route_rows(since - span, since))
        info = {"kind": "prev", "from": since - span, "to": since}
    elif compare and compare != "none":
        d = next((d for d in deploys() if d["c"] == compare), None)
        if d:
            length = min(span, max(now - d["t"], ROUTE_BUCKET_S))
            cur = _sum_routes(_route_rows(d["t"] - ROUTE_BUCKET_S, d["t"] + length), commit=d["c"])
            info = {"kind": "deploy", "deploy": d, "length_s": round(length)}
            # The first commit recorded has nothing before it. (commit=None would
            # mean "any commit" and compare the deploy with its own first minutes.)
            if d.get("prev"):
                base = _sum_routes(
                    _route_rows(d["t"] - length - ROUTE_BUCKET_S, d["t"] + ROUTE_BUCKET_S), commit=d["prev"]
                )
            else:
                info["no_base"] = True
        else:
            cur = _sum_routes(_route_rows(since, now + 1))
    else:
        cur = _sum_routes(_route_rows(since, now + 1))

    cur_total = sum(c[rec.R_SUM] for c in cur.values())
    base_total = sum(c[rec.R_SUM] for c in (base or {}).values())
    keys = sorted(cur, key=lambda k: -cur[k][rec.R_SUM])[:ROUTE_ROWS]
    rows = []
    for key in keys:
        method, _, route = key.partition(" ")
        row = {"method": method, "route": route, "cur": _route_stats(cur[key], cur_total)}
        if base is not None and key in base:
            row["base"] = _route_stats(base[key], base_total)
        rows.append(row)
    return {
        "range": range_key if range_key in RANGES else "1h",
        "compare": info,
        "total": {"routes": len(cur), "n": sum(c[rec.R_N] for c in cur.values()), "time_s": round(cur_total / 1000)},
        "base_total": {"n": sum(c[rec.R_N] for c in base.values()), "time_s": round(base_total / 1000)} if base else None,
        "rows": rows,
        "deploys": [d for d in deploys() if d["t"] >= now - RANGES["30d"]][::-1],
    }


# --- sampling ---------------------------------------------------------------

_sample_lock = threading.Lock()
_last = (0.0, None)


def sample():
    """Everything the live page shows, at most every CACHE_S; also writes
    history and records the busy peak of the current minute."""
    global _last
    with _sample_lock:
        now = time.time()
        if _last[1] is not None and now - _last[0] < CACHE_S:
            return _last[1]
        states = read_states(now)
        data = live(now, states)
        h = host()
        p = pg()
        m = rec.minute_of(now)
        _peaks[m] = max(_peaks.get(m, 0), data["totals"]["busy"])
        for k in [k for k in _peaks if k < m - 2 * 3600]:
            del _peaks[k]
        try:
            persist(now, states, h, p)
        except Exception:
            logger.exception("health: writing history failed")
        data.update(
            {
                "now": now,
                "recording": rec.STATUS_DIR is not None,
                "served_by": "sidecar" if is_monitor() else "app",
                "host": h,
                "pg": p,
            }
        )
        _last = (now, data)
        return data


_sampler_started = False


def start_sampler():
    """Sidecar only: keep sampling (and so writing history) with nobody on the page."""
    global _sampler_started
    if _sampler_started:
        return
    _sampler_started = True

    def loop():
        while True:
            try:
                sample()
            except Exception:
                logger.exception("health: sample failed")
            time.sleep(SAMPLE_EVERY_S)

    threading.Thread(target=loop, name="health-sampler", daemon=True).start()
