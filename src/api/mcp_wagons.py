"""Owner-only MCP tools for the wagon drawings catalogue, over the admin page's own
validation and save code (src/api/admin/wagons.py)."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import shutil
from datetime import datetime
from pathlib import Path

import requests
from flask import current_app, jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.datastructures import FileStorage

from src.api.admin import wagons as admin
from src.pg import pg_session
from src.utils import external_url, owner

MAX_IMAGE_BYTES = 5 * 1024 * 1024
SNAPSHOT_ROOT = Path("backup/mcp/wagons")
UPLOAD_TTL = 3600

_IMAGE_DESC = "GIF/PNG as base64 (a data: URI is fine) or an http(s) URL to fetch."

_META = {
    "label": {"type": "string"},
    "category": {"type": "string"},
    "subcategory": {"type": "string"},
    "era": {"type": "string"},
    "notes": {"type": "string"},
    "source": {"type": "string"},
    "author": {"type": "string", "description": "Comma-separated when several people drew it."},
    "license": {"type": "string", "description": f"'{admin.TRAINLOG_LICENSE}' for drawings licensed to Trainlog."},
    "gauge": {"type": ["integer", "null"], "description": "Track gauge in mm."},
    "px_per_meter": {"type": ["number", "null"]},
    "countries": {"type": "string", "description": "Comma-separated ISO codes, e.g. 'FR,CH'."},
}

TOOLS = [
    {
        "name": "get_wagon",
        "description": "Every catalogue field of one wagon drawing (name from search_wagons), with its image URLs and usage.",
        "inputSchema": {
            "type": "object",
            "properties": {"wagon": {"type": "string"}},
            "required": ["wagon"],
        },
    },
    {
        "name": "create_wagon",
        "description": (
            "Add a wagon drawing to the catalogue. image_type 'sides' takes image_l and/or "
            "image_r (the two side views), 'sides_L'/'sides_R'/'plain' take image. Reuse "
            "the spelling of existing category/author/license values (search_wagons, "
            "get_wagon) so the catalogue doesn't split on a typo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_META,
                "name": {"type": "string", "description": "Key; derived from the label if omitted."},
                "image_type": {"type": "string", "enum": sorted(admin._VALID_IMAGE_TYPES), "default": "sides"},
                "image": {"type": "string", "description": _IMAGE_DESC},
                "image_l": {"type": "string", "description": _IMAGE_DESC},
                "image_r": {"type": "string", "description": _IMAGE_DESC},
            },
            "required": ["label"],
        },
    },
    {
        "name": "update_wagon",
        "description": "Change catalogue fields of a wagon drawing; only the fields given are touched, empty string clears one.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wagon": {"type": "string"},
                **_META,
                "image_type": {"type": "string", "enum": sorted(admin._VALID_IMAGE_TYPES)},
            },
            "required": ["wagon"],
        },
    },
    {
        "name": "upload_wagon_image",
        "description": (
            "Preferred way to set a wagon's image from a file you have: returns a URL, "
            "valid one hour, to send the GIF/PNG to with an HTTP PUT (e.g. `curl -T "
            "file.gif <url>`) from your sandbox, so the image never has to be written out "
            "in a tool call. Side 'L' or 'R' for a side view, '' for a plain image. "
            "If the sandbox can't reach the URL, use set_wagon_image instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wagon": {"type": "string"},
                "side": {"type": "string", "enum": ["", "L", "R"]},
            },
            "required": ["wagon", "side"],
        },
    },
    {
        "name": "set_wagon_image",
        "description": (
            "Replace a wagon drawing's image: side 'L' or 'R' for a side view, '' for a "
            "plain image. Takes the image inline, so prefer upload_wagon_image for files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wagon": {"type": "string"},
                "side": {"type": "string", "enum": ["", "L", "R"]},
                "image": {"type": "string", "description": _IMAGE_DESC},
            },
            "required": ["wagon", "side", "image"],
        },
    },
    {
        "name": "delete_wagon",
        "description": "Remove a wagon drawing from the catalogue (trips and trainsets naming it keep the name).",
        "inputSchema": {
            "type": "object",
            "properties": {"wagon": {"type": "string"}},
            "required": ["wagon"],
        },
    },
]

NAMES = {t["name"] for t in TOOLS}
DESTRUCTIVE = {"update_wagon", "set_wagon_image", "delete_wagon"}


def allowed(user) -> bool:
    return user.username == owner


def _image(value: str) -> FileStorage:
    if value.startswith(("http://", "https://")):
        try:
            r = requests.get(value, timeout=20, stream=True)
            r.raise_for_status()
            data = r.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        except requests.RequestException as e:
            raise ValueError(f"could not fetch the image: {e}")
    else:
        try:
            data = base64.b64decode(value.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image is neither an http(s) URL nor valid base64")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image is larger than 5 MB")
    return FileStorage(io.BytesIO(data), filename="upload")


def _countries(args):
    if "countries" in args and args["countries"]:
        args["countries"] = ",".join(dict.fromkeys(
            c.strip().upper() for c in str(args["countries"]).split(",") if c.strip()))
    return args


def _get(wname):
    with pg_session() as pg:
        row = pg.execute("SELECT * FROM wagons WHERE name = :n", {"n": wname}).fetchone()
    if not row:
        raise ValueError("Wagon not found.")
    w = dict(row)
    suffixes = {"plain": [""], "sides": ["_L", "_R"], "sides_L": ["_L"], "sides_R": ["_R"]}
    w["image_urls"] = [
        external_url("static", filename=f"images/wagons/{w['image']}{sfx}.{w['image_ext'] or 'gif'}")
        for sfx in suffixes.get(w["image_type"], [])
    ] if w.get("image") else []
    trips, users, sets, _ = admin._wagon_usage()
    w["usage"] = {"trips": trips.get(wname, 0), "users": len(users.get(wname, ())),
                  "trainsets": sets.get(wname, 0)}
    return w


def _snapshot(tool, wname):
    """Copy the wagon's row (as CSV) and image files aside before MCP changes them.
    Runs before the write, so a failed snapshot aborts the change."""
    with pg_session() as pg:
        row = pg.execute("SELECT * FROM wagons WHERE name = :n", {"n": wname}).fetchone()
    if not row:
        raise ValueError("Wagon not found.")
    row = dict(row)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = SNAPSHOT_ROOT / admin._sanitize_name(wname) / f"{stamp}_{tool}"
    folder.mkdir(parents=True)
    with open(folder / "wagon.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    if row.get("image"):
        for sfx in ("", "_L", "_R"):
            for ext in admin._VALID_IMAGE_EXTS:
                src = admin.WAGONS_ROOT / f"{row['image']}{sfx}.{ext}"
                if src.is_file():
                    shutil.copy2(src, folder / src.name)
    return folder


def _snapshotted(tool, wname, change):
    folder = _snapshot(tool, wname)
    try:
        return change()
    except Exception:
        # Nothing changed, so nothing to recover.
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="mcp-wagon-image")


def upload(token):
    """PUT/POST target of upload_wagon_image's URL; the signed token is the auth."""
    try:
        target = _signer().loads(token, max_age=UPLOAD_TTL)
    except BadSignature:
        return jsonify({"error": "invalid or expired upload URL"}), 403
    f = request.files.get("file")
    data = f.read() if f else request.get_data()
    if not data:
        return jsonify({"error": "empty body"}), 400
    if len(data) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image is larger than 5 MB"}), 413
    try:
        saved = _snapshotted("upload_wagon_image", target["w"], lambda: admin.replace_image(
            target["w"], target["s"], FileStorage(io.BytesIO(data), filename="upload")))
    except (ValueError, LookupError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(saved)


def call(name, args):
    if name in DESTRUCTIVE:
        return _snapshotted(name, args.get("wagon"), lambda: _call(name, args))
    return _call(name, args)


def _call(name, args):
    if name == "get_wagon":
        return _get(args.get("wagon"))

    if name == "create_wagon":
        label = (args.get("label") or "").strip()
        if not label:
            raise ValueError("label is required")
        image_type = args.get("image_type") or "sides"
        if image_type not in admin._VALID_IMAGE_TYPES:
            raise ValueError("invalid image_type")
        fields = admin.clean_updates(_countries(dict(args)))
        base = {k: fields.get(k) for k in ("category", "subcategory", "era", "notes",
                                            "px_per_meter", "author", "license",
                                            "gauge", "countries")}
        if image_type == "sides":
            slots = [("_L", args.get("image_l")), ("_R", args.get("image_r"))]
        else:
            slots = [({"sides_L": "_L", "sides_R": "_R"}.get(image_type, ""), args.get("image"))]
        sides = [(sfx, _image(v)) for sfx, v in slots if v]
        try:
            wname = admin.insert_wagon(label, (args.get("name") or "").strip(),
                                       base, image_type, sides)
        except FileExistsError as e:
            raise ValueError(str(e))
        return _get(wname)

    if name == "update_wagon":
        wname = args.get("wagon")
        fields = {k: v for k, v in _countries(dict(args)).items() if k in _META or k == "image_type"}
        if not admin.apply_updates(wname, admin.clean_updates(fields)):
            raise ValueError("Wagon not found.")
        return _get(wname)

    if name == "upload_wagon_image":
        wname, side = args.get("wagon"), (args.get("side") or "").upper()
        if side not in ("", "L", "R"):
            raise ValueError("side must be '', 'L' or 'R'")
        _get(wname)
        url = external_url("mcp.wagon_image_upload",
                           token=_signer().dumps({"w": wname, "s": side}))
        return {"url": url, "expires_in_s": UPLOAD_TTL, "example": f"curl -fsS -T image.gif '{url}'"}

    if name == "set_wagon_image":
        side = (args.get("side") or "").upper()
        if side not in ("", "L", "R"):
            raise ValueError("side must be '', 'L' or 'R'")
        try:
            admin.replace_image(args.get("wagon"), side, _image(args.get("image") or ""))
        except LookupError:
            raise ValueError("Wagon not found.")
        return _get(args.get("wagon"))

    if name == "delete_wagon":
        with pg_session() as pg:
            if pg.execute("DELETE FROM wagons WHERE name = :n", {"n": args.get("wagon")}).rowcount == 0:
                raise ValueError("Wagon not found.")
        return {"ok": True}
