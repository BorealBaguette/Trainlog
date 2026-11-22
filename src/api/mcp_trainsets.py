"""Owner-only MCP tools for saved trainsets, over the trainset builder's own save code
(src/api/trainset.py). Every public set can be read; only the sets the owner created
can be changed, each change snapshotted first like the wagon tools."""

from __future__ import annotations

import json
import shutil
from datetime import datetime

from src.api.mcp_wagons import SNAPSHOT_ROOT, check_cap
from src.api.trainset import _name_taken, save_trainset
from src.pg import pg_session
from src.utils import owner

TRAINSET_SNAPSHOTS = SNAPSHOT_ROOT.parent / "trainsets"
SHAPES = ("loco_l", "loco_both", "loco_r", "mid")

_UNITS = {
    "type": "array",
    "description": (
        "The cars in order: {wagon, side: 'L'|'R'} for a drawing (search_wagons), or "
        "{placeholder: text, side?, shape?} for a car with no drawing."
    ),
    "items": {
        "type": "object",
        "properties": {
            "wagon": {"type": "string"},
            "side": {"type": "string", "enum": ["L", "R"]},
            "placeholder": {"type": "string"},
            "shape": {"type": "string", "enum": list(SHAPES)},
        },
    },
}

TOOLS = [
    {
        "name": "get_trainset",
        "description": "One saved trainset (id from list_trainsets) with its cars in the form create/update_trainset take.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "create_trainset",
        "description": "Save a new trainset, personal or public (public ones every user can pick).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "public": {"type": "boolean", "default": False},
                "units": _UNITS,
            },
            "required": ["name", "units"],
        },
    },
    {
        "name": "update_trainset",
        "description": (
            "Rename a trainset you created and/or replace its cars. A rename carries along "
            "the trips naming it (every user's for a public set)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}, "units": _UNITS},
            "required": ["id"],
        },
    },
    {
        "name": "delete_trainset",
        "description": "Delete a trainset you created (trips naming it keep the name).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]

NAMES = {t["name"] for t in TOOLS}
DESTRUCTIVE = {"update_trainset", "delete_trainset"}


def _slim(units):
    if not isinstance(units, list) or not units:
        raise ValueError("units must be a non-empty list.")
    slim = []
    for i, u in enumerate(units):
        if not isinstance(u, dict):
            raise ValueError(f"units[{i}] must be an object.")
        if u.get("side", "L") not in ("L", "R"):
            raise ValueError(f"units[{i}].side must be 'L' or 'R'.")
        if u.get("placeholder"):
            s = {"name": str(u["placeholder"]).strip(), "_side": u.get("side", "L")}
            if u.get("shape"):
                if u["shape"] not in SHAPES:
                    raise ValueError(f"units[{i}].shape must be one of {list(SHAPES)}.")
                s["_phType"] = u["shape"]
        else:
            s = {"name": u.get("wagon"), "_side": u.get("side", "L")}
        slim.append(s)
    with pg_session() as pg:
        known = {r[0] for r in pg.execute("SELECT name FROM wagons WHERE name = ANY(:n)",
                                          {"n": [u["name"] for u in slim]})}
    unknown = [u.get("wagon") for u in units if not u.get("placeholder") and u.get("wagon") not in known]
    if unknown:
        raise ValueError(f"No wagon(s) {unknown}. See search_wagons, or give a placeholder.")
    return slim


def _row(pg, tid, mine=False):
    row = pg.execute(
        "SELECT id, name, username, is_admin, units_json, created_at, updated_at FROM trainsets"
        f" WHERE id = :id AND ({'' if mine else 'is_admin OR '}username = :u)",
        {"id": tid, "u": owner}).fetchone()
    if not row:
        raise ValueError("No such trainset you created. See list_trainsets." if mine
                         else "No such trainset. See list_trainsets.")
    return dict(row)


def _snapshot(tool, row):
    """Write the trainset's row aside as JSON before MCP changes it; a failed snapshot
    aborts the change. Also counts towards the wagon tools' DAILY_WRITES."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = TRAINSET_SNAPSHOTS / str(row["id"]) / f"{stamp}_{tool}"
    folder.mkdir(parents=True)
    (folder / "trainset.json").write_text(json.dumps(row, default=str, ensure_ascii=False, indent=2))
    return folder


def _get(tid):
    with pg_session() as pg:
        r = _row(pg, tid)
        units = json.loads(r["units_json"] or "[]")
        known = {w[0] for w in pg.execute("SELECT name FROM wagons WHERE name = ANY(:n)",
                                          {"n": [u.get("name") for u in units]})}
    cars = []
    for u in units:
        car = {"wagon": u.get("name")} if u.get("name") in known else {"placeholder": u.get("name")}
        car["side"] = u.get("_side", "L")
        if u.get("_phType"):
            car["shape"] = u["_phType"]
        cars.append(car)
    return {"id": r["id"], "name": r["name"], "public": bool(r["is_admin"]),
            "owner": r["username"], "updated_at": r["updated_at"], "units": cars}


def _name(value):
    name = (value or "").strip()
    if not name:
        raise ValueError("name is required.")
    return name


def call(name, args):
    if name != "get_trainset":
        check_cap()

    if name == "get_trainset":
        return _get(args.get("id"))

    if name == "create_trainset":
        tname, public = _name(args.get("name")), bool(args.get("public"))
        units = _slim(args.get("units"))
        with pg_session() as pg:
            if _name_taken(pg, tname, owner, public):
                raise ValueError(f"A {'public' if public else 'personal'} trainset is already named {tname!r}.")
            tid = pg.execute(
                "INSERT INTO trainsets (name, username, is_admin, units_json)"
                " VALUES (:name, :u, :public, :units) RETURNING id",
                {"name": tname, "u": owner, "public": public, "units": json.dumps(units)}).scalar()
            # Records what was added, and counts it towards DAILY_WRITES.
            _snapshot(name, _row(pg, tid))
        return _get(tid)

    new_units = _slim(args["units"]) if name == "update_trainset" and "units" in args else None
    with pg_session() as pg:
        r = _row(pg, args.get("id"), mine=True)
        if name == "update_trainset":
            tname = _name(args["name"]) if "name" in args else r["name"]
            units = new_units if new_units is not None else json.loads(r["units_json"] or "[]")
            if _name_taken(pg, tname, r["username"], r["is_admin"], exclude_id=r["id"]):
                raise ValueError(f"Another trainset of the same scope is already named {tname!r}.")
        folder = _snapshot(name, r)
        try:
            if name == "update_trainset":
                save_trainset(pg, r["id"], {**r, "old_name": r["name"]}, tname, units)
            else:
                pg.execute("DELETE FROM trainsets WHERE id = :id", {"id": r["id"]})
        except Exception:
            # Nothing changed, so nothing to recover.
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return _get(r["id"]) if name == "update_trainset" else {"ok": True}
