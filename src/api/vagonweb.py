"""
VagonWeb assisted importer — owner only.

Unlike the previous bulk importer (removed in "Add PNG support for wagons and
streamlined display"), nothing is written to the wagons table automatically.
A VagonWeb page is fetched and parsed into a *staging* area, and the owner then
imports wagons one at a time, filling in author and licence individually — we
only have permission for specific drawings, not for the site as a whole.

Long EMU strips (one GIF holding a whole multi-car unit) can be cut into one
wagon per car. Cut positions are suggested automatically and are editable in the
UI before import.

Endpoints (all owner-only, mounted under /api/admin/vagonweb):
    POST   /preview   {url}          → parse a page, stage its images
    POST   /import    {key, units…}  → import one staged wagon (optionally split)
    DELETE /staging                  → drop everything staged
"""

import hashlib
import html as html_mod
import json
import logging
import posixpath
import re
import time
from datetime import date
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request, session
from PIL import Image

from py.utils import get_all_countries
from src.ai import call_ai_json, get_ai_api_key
from src.pg import pg_session
from src.utils import owner_required
from src.wagon_cuts import crop_side, distinct_sides, segments, suggest_cuts

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

BASE = "https://www.vagonweb.cz"
VW_IMG_BASE = BASE + "/popisy/img/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TrainlogWagonImport/2.0)",
    "Referer": BASE + "/razeni/vlak.php",
}

WAGONS_ROOT = Path("static/images/wagons").resolve()
VW_PREFIX = "images/vagonweb"
STAGING_DIR = WAGONS_ROOT / "images/_vw_staging"
STAGING_TTL = 24 * 3600  # staged files are pruned after a day

CLASS_MAP = {
    "tab-1tr": "1st",
    "tab-2tr": "2nd",
    "tab-2ptr": "2nd-plus",
    "tab-club": "business",
    "tab-jidel": "dining",
    "tab-luzk": "sleeper",
    "tab-lehat": "couchette",
    "tab-sluz": "service",
}

_CLASS_PAT = re.compile(
    r"<td class='(tab-(?:" + "|".join(k.split("-", 1)[1] for k in CLASS_MAP) + "))'>"
)

_GIF_MAGIC = (b"GIF87a", b"GIF89a")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ── Image fetching / staging ──────────────────────────────────────────────────


def _resolve_img_url(src: str) -> str:
    """Convert a relative VagonWeb image src to an absolute URL."""
    if src.startswith("http"):
        return src
    normalized = posixpath.normpath(src)
    if normalized.startswith(".."):
        normalized = posixpath.normpath("razeni/" + normalized)
    if normalized.startswith("/"):
        return BASE + normalized
    return BASE + "/" + normalized.lstrip("/")


def _img_local_base(url: str) -> str | None:
    """
    Extract the image base path (without side suffix) from a VagonWeb URL.

    "https://.../popisy/img/ZSSK/Bpeer-2970-B-a.gif"  →  "ZSSK/Bpeer-2970-B"
    Returns None if the URL doesn't originate from the VagonWeb image CDN.
    """
    if not url.startswith(VW_IMG_BASE):
        return None
    path = url[len(VW_IMG_BASE) :]
    path = re.sub(r"-[ab]\.(gif|png)$", "", path, flags=re.IGNORECASE)
    return path or None


def _name_from_base(base: str) -> str:
    """Sanitise a local image base path to a valid DB primary key."""
    return re.sub(r"[^\w]+", "_", base).strip("_") or "wagon"


def _sanitize_name(name: str) -> str:
    """Sanitise an owner-typed name the same way the wagons admin does."""
    return re.sub(r"[^\w.-]+", "_", name).strip("_")


def _variant_base(vw_base: str, code: str) -> str:
    """
    Image base for one type code sharing a drawing.

    VagonWeb draws a B3-2 with the B3-4 file, so a single image backs several codes.
    Each code becomes its own catalogue entry with its own copy of the picture —
    "N-/Vy/B3-4" + code "B3-2" → "N-/Vy/B3-2". A few KB duplicated is a fair price
    for each wagon carrying its own designation.
    """
    stem = _sanitize_name(code or "")
    if not stem:
        return vw_base
    parent = posixpath.dirname(vw_base)
    return f"{parent}/{stem}" if parent else stem


def _download(url: str, dest_noext: Path) -> str | None:
    """Download a VagonWeb image. Returns the extension saved, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        content = resp.content
        if content[:6] in _GIF_MAGIC:
            ext = "gif"
        elif content[:8] == _PNG_MAGIC:
            ext = "png"
        else:
            return None
        dest_noext.parent.mkdir(parents=True, exist_ok=True)
        dest_noext.with_suffix(f".{ext}").write_bytes(content)
        return ext
    except requests.RequestException:
        return None


def _prune_staging() -> None:
    """Delete staged files older than STAGING_TTL."""
    if not STAGING_DIR.is_dir():
        return
    cutoff = time.time() - STAGING_TTL
    for f in STAGING_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _stage_key(vw_base: str) -> str:
    return hashlib.sha1(vw_base.encode()).hexdigest()[:16]


def _staged_meta(key: str) -> dict | None:
    """Load a staged entry's sidecar. Returns None for unknown or malformed keys."""
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        return None
    sidecar = STAGING_DIR / f"{key}.json"
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return None


# ── HTML parsing ──────────────────────────────────────────────────────────────


def _strip_tags(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def _parse_train_title(html: str) -> str:
    """Extract a clean train title like "IC 186 Hernád"; falls back to <title>."""
    m = re.search(r"<span[^>]+class='velky15'>(.*?)</span>", html, re.DOTALL)
    if m:
        content = m.group(1)
        cat_m = re.search(r"<img[^>]+alt='([^']+)'", content)
        category = cat_m.group(1) if cat_m else ""
        name_m = re.search(r"<i>([^<]+)</i>", content)
        name = name_m.group(1).strip() if name_m else ""
        num_m = re.search(r"\b(\d+)\b", _strip_tags(content))
        number = num_m.group(1) if num_m else ""
        parts = [p for p in [category, number, name] if p]
        if parts:
            return " ".join(parts)
    # Fallback: the page <title> is a breadcrumb with HTML entities,
    # "vagonWEB &raquo; Řazení vlaků &raquo; 2026 &raquo; Vy Rt &raquo; Rt 60 F4".
    # Unescape it and keep the last crumb, which names the train.
    m = re.search(r"<title>([^<]+)</title>", html)
    if not m:
        return "Unknown Train"
    crumbs = [c.strip() for c in html_mod.unescape(m.group(1)).split("»") if c.strip()]
    return crumbs[-1] if crumbs else "Unknown Train"


def _parse_amenities(section: str) -> str:
    """Human-readable amenity string from the tab-pocmist span."""
    m = re.search(r"<span class='tab-pocmist'>(.*?)</span>", section, re.DOTALL)
    if not m:
        return ""
    items = []
    for chunk in re.split(r"<img\b", m.group(1)):
        title_m = re.search(r"\btitle='([^']+)'", chunk)
        if not title_m:
            continue
        title = title_m.group(1)
        after = re.sub(r"^[^>]*>", "", chunk)
        num_m = re.search(r"([\d+]+)", after.strip())
        items.append(f"{title}: {num_m.group(1)}" if num_m else title)
    return ", ".join(items)


def _parse_description_section(section: str) -> dict:
    """Parse one description section: operator, type, variant, coach no, route…"""
    cn = re.search(r"<span class=raz-cislo>([\d/]+)</span>", section)

    # The operator sits in the span immediately before the type code, and its title
    # attribute names the country in Czech ("Norsko - Vy"). Anchor on the tab-radam
    # span that follows: matching the operator's own text is unreliable because it is
    # not always upper-case ("Vy", "ÖBB", "SNCF"), and dropping it loses the country.
    op = re.search(
        r"<span\s+title='([^']*)'\s*>([^<]*)</span>\s*<span class=tab-radam>", section
    )
    wt = re.search(r"<span class=tab-radam>([^<]+)<sup>", section)
    sv = re.search(r"<small>([^<]*)</small>", section)
    rt = re.search(r"<b>([^<]+)</b>", section)
    # The photo-gallery link is the best identity hint on the page: its path is
    # country/operator/family, e.g. "N/NSB/B3-Flamsbana" for a Flåm Railway coach.
    gl = re.search(r"fotogalerie/([^'\"?]+)", section)

    return {
        "operator": _strip_tags(op.group(2)) if op else "",
        "operator_country": html_mod.unescape(op.group(1)).strip() if op else "",
        "wagon_type": wt.group(1).strip() if wt else "",
        "sub_variant": sv.group(1).strip() if sv else "",
        "coach_no": cn.group(1) if cn else None,
        "route": rt.group(1).strip() if rt else "",
        "gallery": gl.group(1).strip() if gl else "",
        "amenities": _parse_amenities(section),
    }


def _parse_cell(cell: str, vlak_id: str, cell_idx: int, full_html: str) -> list[dict]:
    """Parse one <td class='bunka_vozu'> cell into wagon dicts (2+ for animated cells)."""
    raw_classes = set(_CLASS_PAT.findall(cell))
    classes = sorted(CLASS_MAP[c] for c in raw_classes if c in CLASS_MAP)

    im = re.search(
        r"<img class='((?:obraceci )?obrazek_vagonu)'([^>]*)src='([^']+)'", cell
    )
    if not im:
        return []

    class_attr, attr_rest, img_src = im.group(1), im.group(2), im.group(3)
    img_url_a = _resolve_img_url(img_src)
    has_both = "obraceci" in class_attr

    is_animated = bool(re.search(r"id='obraz_", attr_rest)) or (
        f"obraz_{vlak_id}_{cell_idx}" in cell
    )

    images: list[tuple[str, str | None, bool]] = []
    if is_animated:
        js_pat = re.compile(
            rf"obr_{re.escape(vlak_id)}_{re.escape(str(cell_idx))}\[\d+\]\.src='([^']+)'"
        )
        js_srcs = js_pat.findall(full_html)
        if js_srcs:
            images = [(_resolve_img_url(src), None, False) for src in js_srcs]
        else:
            images = [(img_url_a, None, False)]
    else:
        # A two-sided wagon is drawn twice, as "…-a.gif" and "…-b.gif". The page may
        # link EITHER of them, so normalise to the canonical pair rather than assuming
        # the linked one is "-a" — deriving only "-a"→"-b" silently refetches the same
        # file whenever the page happens to link "-b".
        suffix = re.search(r"-([ab])\.(gif|png)$", img_url_a)
        img_url_b = None
        if has_both and suffix:
            stem, ext = img_url_a[: suffix.start()], suffix.group(2)
            img_url_a, img_url_b = f"{stem}-a.{ext}", f"{stem}-b.{ext}"
        images = [(img_url_a, img_url_b, has_both)]

    info_match = re.search(r"</table>(.*)", cell, re.DOTALL)
    sections = re.split(r"<hr>", info_match.group(1) if info_match else cell)

    wagons = []
    for i, (url_a, url_b, both) in enumerate(images):
        desc = _parse_description_section(
            sections[i] if i < len(sections) else sections[-1]
        )
        wagons.append(
            {
                "img_url_a": url_a,
                "img_url_b": url_b,
                "has_both_sides": both,
                "classes": classes,
                **desc,
            }
        )
    return wagons


def _parse_page(html: str) -> dict:
    """Parse a full VagonWeb train page into {train_title, wagons: [...]} (deduped)."""
    train_title = _parse_train_title(html)

    blocks = list(re.finditer(r"<div id='vlak_(\d+)'", html))
    if not blocks:
        return {"train_title": train_title, "wagons": [], "compositions": []}

    # Keyed by VagonWeb image base so the same coach appearing in several
    # compositions is offered once.
    unique: dict[str, dict] = {}
    # …but the running order, with its repeats, is what a trainset needs, so keep
    # each composition's cells in sequence alongside the deduped catalogue view.
    compositions: list[dict] = []
    for idx, m in enumerate(blocks):
        vlak_id = m.group(1)
        start = m.start()
        end = blocks[idx + 1].start() if idx + 1 < len(blocks) else len(html)
        block = html[start:end]

        tbl_m = re.search(r"<table class='vlacek'[^>]*>(.*)", block, re.DOTALL)
        tbl = tbl_m.group(1) if tbl_m else block

        cells: list[dict] = []
        for cell_idx, cell in enumerate(re.split(r"<td class='bunka_vozu[^']*'", tbl)[1:], 1):
            for w in _parse_cell(cell, vlak_id, cell_idx, block):
                vw_base = _img_local_base(w["img_url_a"])
                if not vw_base:
                    continue
                cells.append({
                    "vw_base": vw_base,
                    "code": (w["wagon_type"] or "").strip(),
                    "coach_no": w["coach_no"],
                })
                if vw_base not in unique:
                    unique[vw_base] = {**w, "vw_base": vw_base, "variants": []}
                # VagonWeb reuses one drawing across related type codes (a B3-2 is
                # drawn with the B3-4 file). Deduping by image is right — there is
                # only one file to import — but record every code that maps onto it
                # so the collapse is visible instead of silent.
                variants = unique[vw_base]["variants"]
                code = (w["wagon_type"] or "").strip()
                if code and code not in variants:
                    variants.append(code)

        if cells:
            h4 = re.search(r"<h4[^>]*>(.*?)</h4>", block, re.DOTALL)
            title = _strip_tags(h4.group(1)) if h4 else f"Composition {vlak_id}"
            compositions.append({
                "vlak_id": vlak_id,
                "title": re.sub(r"\s+", " ", title).strip(),
                "cells": cells,
            })

    return {
        "train_title": train_title,
        "wagons": list(unique.values()),
        "compositions": compositions,
    }


# ── Blueprint ─────────────────────────────────────────────────────────────────

vagonweb_blueprint = Blueprint("vagonweb", __name__)


@vagonweb_blueprint.route("/preview", methods=["POST"])
@owner_required
def preview():
    """Fetch and parse a VagonWeb page, staging every image it references."""
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    if not url.startswith(BASE):
        return jsonify({"error": f"only {BASE} URLs are accepted"}), 400

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return jsonify({"error": f"HTTP error fetching page: {e}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"could not fetch page: {e}"}), 502

    _prune_staging()
    parsed = _parse_page(resp.text)

    out = []
    for w in parsed["wagons"]:
        vw_base = w["vw_base"]
        key = _stage_key(vw_base)

        ext_l = _download(w["img_url_a"], STAGING_DIR / f"{key}_L")
        if not ext_l:
            continue
        ext_r = (
            _download(w["img_url_b"], STAGING_DIR / f"{key}_R")
            if w["has_both_sides"] and w["img_url_b"]
            else None
        )

        path_l = STAGING_DIR / f"{key}_L.{ext_l}"
        # Drop a "second side" that turned out to be a copy — the R view will be
        # generated by flipping instead, so downstream code sees one clear signal.
        if ext_r and not distinct_sides(path_l, STAGING_DIR / f"{key}_R.{ext_r}"):
            (STAGING_DIR / f"{key}_R.{ext_r}").unlink(missing_ok=True)
            ext_r = None

        with Image.open(path_l) as im:
            width, height = im.size

        default_name = _name_from_base(vw_base)
        label = " ".join(
            p for p in (w["operator"], w["wagon_type"], w["sub_variant"]) if p
        )
        meta = {
            "key": key,
            "vw_base": vw_base,
            "ext_l": ext_l,
            "ext_r": ext_r,
            # 'sides' only for a real "-a"/"-b" pair; a lone drawing stays 'plain'
            "image_type": "sides" if ext_r else "plain",
            "two_sided_source": bool(ext_r),
            "width": width,
            "height": height,
            "default_name": default_name,
            "label": label or default_name,
            "operator": w["operator"],
            "operator_country": w["operator_country"],
            "gallery": w["gallery"],
            "coach_no": w["coach_no"],
            "category": w["wagon_type"] or None,
            "variants": w.get("variants") or [],
            # Each code gets its own wagon; precompute the key so the UI and the
            # importer agree on it without the client inventing image paths.
            "variant_defaults": [
                {"code": c, "name": _name_from_base(_variant_base(vw_base, c))}
                for c in (w.get("variants") or [])
            ],
            "subcategory": w["sub_variant"] or None,
            "notes": w["amenities"] or ", ".join(w["classes"]) or None,
            "route": w["route"],
            "classes": w["classes"],
            "train_title": parsed["train_title"],
            "source_url": url,
        }
        (STAGING_DIR / f"{key}.json").write_text(json.dumps(meta))

        stage_rel = STAGING_DIR.relative_to(WAGONS_ROOT).as_posix()
        out.append(
            {
                **meta,
                "stage_url_l": f"{stage_rel}/{key}_L.{ext_l}",
                "stage_url_r": f"{stage_rel}/{key}_R.{ext_r}" if ext_r else None,
                "suggested_cuts": suggest_cuts(path_l),
            }
        )

    # Flag what is already in the catalogue (single round trip).
    if out:
        # Each type code sharing a drawing is imported under its OWN path, so it is
        # not enough to look for the vw_base — a B7-5/B7-4/A7-1 trio lands on three
        # separate paths and none of them is the base. Check every variant, and a
        # split import additionally stores its cars under "<path>_c1", "_c2"…
        def _paths(w):
            bases = [w["vw_base"]] + [
                _variant_base(w["vw_base"], c) for c in (w.get("variants") or [])
            ]
            return {f"{VW_PREFIX}/{b}" for b in bases}

        all_paths = sorted({p for w in out for p in _paths(w)})
        with pg_session() as pg:
            rows = pg.execute(
                """
                SELECT image FROM wagons
                WHERE image = ANY(:paths) OR image LIKE ANY(:like_paths)
                """,
                {
                    "paths": all_paths,
                    "like_paths": [f"{p}\\_c%" for p in all_paths],
                },
            ).fetchall()
        known = {r["image"] for r in rows}

        def _have(path):
            return any(img == path or img.startswith(path + "_c") for img in known)

        for w in out:
            codes = w.get("variants") or []
            if codes:
                done = [c for c in codes
                        if _have(f"{VW_PREFIX}/{_variant_base(w['vw_base'], c)}")]
            else:
                done = ["*"] if _have(f"{VW_PREFIX}/{w['vw_base']}") else []
            w["imported_codes"] = [c for c in done if c != "*"]
            w["already_imported"] = bool(done) and len(done) == max(len(codes), 1)

    return jsonify({
        "train_title": parsed["train_title"],
        "wagons": out,
        "compositions": parsed.get("compositions") or [],
    })


@vagonweb_blueprint.route("/import", methods=["POST"])
@owner_required
def import_one():
    """
    Import a staged drawing as one wagon per type code per car.

    Body: {key, author, license, source, era, countries, gauge, px_per_meter,
           cuts: [x…], variants: [code…],
           units: [{name, label, category, subcategory, notes}, …]}
    `units` is the variant x car grid flattened variant-major, so it must hold
    exactly len(variants) * (len(cuts) + 1) entries.
    """
    data = request.get_json(silent=True) or {}
    meta = _staged_meta(data.get("key"))
    if not meta:
        return jsonify({"error": "unknown or expired staging key"}), 404

    author = (data.get("author") or "").strip()
    license_ = (data.get("license") or "").strip()
    if not author or not license_:
        return jsonify({"error": "author and license are both required"}), 400

    width = meta["width"]
    try:
        cuts = sorted({int(c) for c in (data.get("cuts") or [])})
    except (TypeError, ValueError):
        return jsonify({"error": "cut positions must be integers"}), 400
    if any(not 0 < c < width for c in cuts):
        return jsonify({"error": "cut positions must lie inside the image"}), 400

    units = data.get("units") or []
    spans = segments(cuts, width)
    # One entry per type code that shares this drawing; [""] means "just the drawing
    # as VagonWeb names it", which is the ordinary single-code case.
    variants = [str(v).strip() for v in (data.get("variants") or []) if str(v).strip()]
    if not variants:
        variants = [""]
    if len(units) != len(variants) * len(spans):
        return jsonify({
            "error": f"expected {len(variants) * len(spans)} unit(s) "
                     f"({len(variants)} code(s) x {len(spans)} car(s)), got {len(units)}"
        }), 400

    names = [_sanitize_name((u.get("name") or "").strip()) for u in units]
    if any(not n for n in names) or len(set(names)) != len(names):
        return jsonify({"error": "each unit needs a distinct name"}), 400

    try:
        gauge = data.get("gauge")
        gauge = int(gauge) if str(gauge or "").strip() else None
        ppm = data.get("px_per_meter")
        ppm = float(ppm) if str(ppm or "").strip() else None
    except (TypeError, ValueError):
        return jsonify({"error": "gauge and px_per_meter must be numbers"}), 400

    # Comma-separated ISO alpha-2, matching tickets.active_countries
    raw_countries = data.get("countries")
    if isinstance(raw_countries, str):
        raw_countries = raw_countries.split(",")
    valid_countries = set(get_all_countries())
    countries = ",".join(
        c for c in dict.fromkeys(str(x).strip().upper() for x in (raw_countries or []))
        if c in valid_countries
    ) or None

    if ppm is not None and ppm <= 0:
        return jsonify({"error": "px_per_meter must be positive"}), 400

    with pg_session() as pg:
        taken = pg.execute(
            "SELECT name FROM wagons WHERE name = ANY(:names)", {"names": names}
        ).fetchall()
    if taken:
        return jsonify(
            {"error": "already exists: " + ", ".join(r["name"] for r in taken)}
        ), 409

    src_l = STAGING_DIR / f"{meta['key']}_L.{meta['ext_l']}"
    src_r = (
        STAGING_DIR / f"{meta['key']}_R.{meta['ext_r']}" if meta["ext_r"] else None
    )
    if not src_l.is_file():
        return jsonify({"error": "staged image is gone — re-run the preview"}), 410

    # Only a real "-a"/"-b" pair gives two sides. With a single drawing the wagon is
    # 'plain' — one file, no side suffix. We do NOT invent the other side by mirroring:
    # a flipped drawing is a fabrication, and 'plain' is the honest record.
    two_sided = bool(src_r) and distinct_sides(src_l, src_r)

    ext = meta["ext_l"]
    image_type = "sides" if two_sided else "plain"
    rows = []
    for v_idx, code in enumerate(variants):
        # Each code is written out separately, picture and all — the drawing is a few
        # KB, and a shared image path would make the codes indistinguishable on disk.
        variant_base = _variant_base(meta["vw_base"], code)
        for c_idx, (x0, x1) in enumerate(spans):
            unit = units[v_idx * len(spans) + c_idx]
            suffix = f"_c{c_idx + 1}" if len(spans) > 1 else ""
            image_path = f"{VW_PREFIX}/{variant_base}{suffix}"
            base = WAGONS_ROOT / image_path

            if two_sided:
                crop_side(src_l, base.with_name(base.name + f"_L.{ext}"), x0, x1)
                crop_side(
                    src_r, base.with_name(base.name + f"_R.{ext}"), x0, x1, mirror_box=True
                )
            else:
                crop_side(src_l, base.with_name(base.name + f".{ext}"), x0, x1)

            rows.append(
                {
                    "name": names[v_idx * len(spans) + c_idx],
                    "label": (unit.get("label") or "").strip()
                             or names[v_idx * len(spans) + c_idx],
                    "category": (unit.get("category") or "").strip() or None,
                    "subcategory": (unit.get("subcategory") or "").strip() or None,
                    "notes": (unit.get("notes") or "").strip() or None,
                    "era": (data.get("era") or "").strip() or None,
                    "countries": countries,
                    "source": (data.get("source") or "VagonWeb").strip(),
                    "author": author,
                    "license": license_,
                    "gauge": gauge,
                    "px_per_meter": ppm,
                    "image": image_path,
                    "image_type": image_type,
                    "image_ext": ext,
                    "updated_on": date.today().isoformat(),
                }
            )

    with pg_session() as pg:
        for row in rows:
            pg.execute(
                """
                INSERT INTO wagons
                    (name, label, category, subcategory, notes, era,
                     countries, source, author, license, gauge, px_per_meter,
                     image, image_type, image_ext, updated_on)
                VALUES
                    (:name, :label, :category, :subcategory, :notes, :era,
                     :countries, :source, :author, :license, :gauge, :px_per_meter,
                     :image, :image_type, :image_ext, :updated_on)
                """,
                row,
            )

    return jsonify({"imported": [r["name"] for r in rows]}), 201


# ── AI field suggestions ──────────────────────────────────────────────────────

# Era uses the MLG/European epoch codes already in the catalogue, so the model has
# to pick from them rather than invent a scheme.
_ERA_CODES = ["3a", "3b", "4a", "4b", "5a", "5b", "6", "6a", "6b"]


def _catalogue_categories(limit: int = 25) -> list[str]:
    """
    The catalogue's most-used categories, offered to the model so it reuses an
    existing family name instead of coining one.

    Only categories: seeding subcategories the same way backfires, because the model
    reaches for a familiar-looking one ("Second class") over the accurate answer.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT category FROM wagons
            WHERE category IS NOT NULL AND category <> ''
            GROUP BY category ORDER BY COUNT(*) DESC LIMIT :limit
            """,
            {"limit": limit},
        ).fetchall()
    return [r["category"] for r in rows]


@vagonweb_blueprint.route("/suggest", methods=["POST"])
@owner_required
def suggest_fields():
    """
    Ask the AI to fill in the descriptive fields for a staged wagon.

    VagonWeb's own description is in Czech and its type codes are terse, so the model
    is given that raw metadata and asked for English prose plus the catalogue's own
    vocabulary. Returns suggestions only — the owner reviews them before importing.
    """
    data = request.get_json(silent=True) or {}
    meta = _staged_meta(data.get("key"))
    if not meta:
        return jsonify({"error": "unknown or expired staging key"}), 404
    if not get_ai_api_key():
        return jsonify({"error": "no AI API key configured"}), 503

    categories = _catalogue_categories()

    # One request covers the whole strip: the cars are described together so the model
    # can tell them apart ("car 1 is the driving car, car 4 carries the pantograph"),
    # which it cannot do if each car is asked about in isolation.
    try:
        cuts = sorted({int(c) for c in (data.get("cuts") or [])})
    except (TypeError, ValueError):
        cuts = []
    bounds = [0, *cuts, int(meta.get("width") or 0)]
    widths = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
    cars = len(widths) or 1

    # The row keys go with them: a hint like "BPb74 = N_Vy_BM74_c4" is only actionable
    # if the model has actually been told which key belongs to which position.
    keys = [str(k).strip() for k in (data.get("car_keys") or [])]

    # Several type codes can share one drawing (a B7-4 and an A7-1 drawn with the same
    # file), and each becomes its own wagon. The answer therefore has one entry per
    # code-and-car, in the same variant-major order the import grid uses — otherwise
    # only the first row would ever get filled.
    variants = [str(v).strip() for v in (data.get("variants") or []) if str(v).strip()]
    if not variants:
        variants = [""]
    grid = [(code, i, w) for code in variants for i, w in enumerate(widths)]
    cars = len(grid)

    if cars > 1:
        car_lines = "\n".join(
            f"    entry {n + 1}"
            + (f" — type code {code}" if code else "")
            + (f", car {i + 1} of {len(widths)}" if len(widths) > 1 else "")
            + (f" (key {keys[n]})" if n < len(keys) and keys[n] else "")
            + f": {w} px wide"
            for n, (code, i, w) in enumerate(grid)
        )
        multi_code = len(variants) > 1
        car_block = (
            f"\nThis drawing produces {cars} catalogue entries:\n{car_lines}\n"
            f"Answer with exactly {cars} entries in this order.\n"
            + (
                "Several type codes share this one drawing. They are DIFFERENT vehicles —\n"
                "a Norwegian 'A7-1' is first class where a 'B7-4' is second — so describe\n"
                "each code on its own terms rather than repeating one description.\n"
                if multi_code else
                "Describe each car individually where they genuinely differ (driving car vs\n"
                "intermediate, first vs second class, a short centre module). Where they do\n"
                "not differ, repeating the same text is fine.\n"
            )
            + "\nORDERING RULES, in priority order:\n"
            "  1. If the extra information below pins a specific car to a specific key\n"
            '     (e.g. "BPb74 = ..._c4"), that pinning is a hard constraint. Honour it\n'
            "     even when it contradicts a list given as left-to-right, and re-derive\n"
            "     the other cars around it — a drawing is often the mirror of the order a\n"
            "     list is written in, so check whether reversing the list satisfies the\n"
            "     pinning before assuming the list order is correct.\n"
            "  2. Widths are your other anchor: a car with a distinctive width (a short\n"
            "     centre module) must land on the position that actually has that width.\n"
            "  3. Only then fall back to the order a list is written in.\n"
        )
    else:
        car_block = ""

    hint = (data.get("hint") or "").strip()
    hint_block = (
        "\nThe cataloguer has supplied extra information. Treat it as authoritative and\n"
        "prefer it over your own recollection wherever the two disagree:\n"
        f"---\n{hint[:4000]}\n---\n"
        if hint
        else ""
    )

    prompt = f"""You are cataloguing a railway vehicle drawing for a trainset database.

Here is everything scraped from vagonweb.cz about it. These are RAW, loosely parsed
fields — the labels below say where each came from, not what it means. The description
text is usually Czech: translate and rewrite it as natural English.

  country + operator : {meta.get("operator_country") or "unknown"}   (Czech country name)
  operator           : {meta.get("operator") or "unknown"}
  type code          : {meta.get("category") or "unknown"}
  same drawing also used for : {", ".join(meta.get("variants") or []) or "n/a"}
  photo gallery path : {meta.get("gallery") or "none"}   (country / operator / family)
  image path         : {meta.get("vw_base")}   (country / operator / type code)
  vagonweb extra     : {meta.get("subcategory") or "none"}   (often not a real variant)
  amenities (cs)     : {meta.get("notes") or "none"}
  travel classes     : {", ".join(meta.get("classes") or []) or "unknown"}
  coach number       : {meta.get("coach_no") or "n/a"}
  seen on train      : {meta.get("train_title") or "unknown"} {meta.get("route") or ""}
  source page        : {meta.get("source_url") or "unknown"}
  drawing            : {meta.get("width")}x{meta.get("height")} px
{car_block}{hint_block}
The country line is authoritative — trust it over any resemblance the type code has to
another country's naming scheme. The gallery path is the strongest identity hint: a path
like "N/NSB/B3-Flamsbana" means a Norwegian NSB B3 coach used on the Flåm Railway.

Work out which real vehicle this is and answer from your own knowledge of that vehicle,
not merely by reshuffling the text above.

Return ONLY a JSON object, no markdown. "shared" describes the whole vehicle, "cars"
holds exactly {cars} entry/entries in left-to-right order:
{{
  "shared": {{
    "category":  "broad family, e.g. 'EMU', 'DMU', 'Coaches', 'Electric locomotives'",
    "gauge":     track gauge in mm as a number. Default to the national standard for the country above. Only depart from it if you positively know this vehicle runs on a narrow- or broad-gauge line — a scenic or mountain route is NOT evidence of narrow gauge,
    "era":       "epoch code(s) from {",".join(_ERA_CODES)}, comma-separated for a range, e.g. '5b,6'",
    "countries": ["ISO 3166-1 alpha-2 codes of the countries this vehicle normally runs in, e.g. ['NO'] or ['CH','DE','AT']. Where it actually operates, not where it was built"]
  }},
  "cars": [
    {{
      "label":       "short display name for THIS entry. It must contain that entry's full type code exactly as given above — never shorten 'B7-5' to 'B7', and never drop a suffix that distinguishes it from a sibling code. No operator prefix, and do not repeat the code twice",
      "subcategory": "series, variant or role of this car, or null. Not the operator name",
      "notes":       "one or two English sentences on this car. No Czech. No personal data. Mention a builder or a build year ONLY if you are certain of it — a short note that just describes the interior is far better than a detailed one that is wrong."
    }}
  ]
}}

Categories already used in this catalogue — reuse one if it fits, otherwise coin a
clear new one: {", ".join(categories) or "none yet"}

"era" describes when the vehicle was/is in service: 3a/3b pre-1970, 4a/4b 1970s-80s,
5a/5b 1985-2000, 6 modern.
Use null for anything you genuinely cannot determine — null is a good answer, and the
owner will fill it in. Never invent a builder, a works, or a date to sound complete."""

    # Model choice here is a latency call as much as a quality one. Qwen3.5-397B is the
    # most accurate of the catalogue models on rolling stock but takes 78-112s on this
    # prompt, straddling the 120s timeout — it failed in production. Kimi-K2.6 is as
    # accurate and just as slow. Of the fast models this is the only one that gets the
    # structured fields right: ~3s, and it identified a BM74 as an EMU where
    # Mistral-Small-4-119B (1.4s) called it "Coaches" and Nemotron misdated the era.
    # Builders and dates in `notes` are still approximate — hence the hint box.
    suggestion = call_ai_json(prompt, model="google/gemma-4-31B-it")
    # A bare list is a common shape slip — read it as the cars array rather than failing
    if isinstance(suggestion, list):
        suggestion = {"cars": suggestion}
    if not isinstance(suggestion, dict):
        logger.warning("VagonWeb suggest: unusable AI response %r", suggestion)
        return jsonify({"error": "the AI did not return usable suggestions"}), 502

    def _text(v):
        return str(v).strip() or None if v is not None else None

    raw_shared = suggestion.get("shared")
    if not isinstance(raw_shared, dict):
        raw_shared = suggestion  # tolerate a flat object if the model ignores the shape

    shared = {
        "category": _text(raw_shared.get("category")),
        "gauge": raw_shared.get("gauge"),
        "era": raw_shared.get("era"),
        "countries": raw_shared.get("countries"),
    }

    if shared["gauge"] is not None:
        try:
            shared["gauge"] = int(float(str(shared["gauge"]).replace("mm", "").strip()))
        except (TypeError, ValueError):
            shared["gauge"] = None

    if shared["era"]:
        kept = [p.strip() for p in str(shared["era"]).split(",") if p.strip() in _ERA_CODES]
        shared["era"] = ",".join(kept) or None

    # Countries must be real ISO alpha-2 codes — the selector only offers those
    valid = set(get_all_countries())
    codes = shared["countries"]
    if isinstance(codes, str):
        codes = codes.split(",")
    shared["countries"] = [
        c for c in dict.fromkeys(str(x).strip().upper() for x in (codes or []))
        if c in valid
    ] or None

    # One entry per car, padded/truncated so the UI can zip it against the rows
    raw_cars = suggestion.get("cars")
    if not isinstance(raw_cars, list):
        raw_cars = [raw_shared]
    out_cars = []
    for i in range(cars):
        c = raw_cars[i] if i < len(raw_cars) and isinstance(raw_cars[i], dict) else {}
        fallback = raw_cars[-1] if raw_cars and isinstance(raw_cars[-1], dict) else {}
        out_cars.append({
            "label": _text(c.get("label") or fallback.get("label")),
            "subcategory": _text(c.get("subcategory") or fallback.get("subcategory")),
            "notes": _text(c.get("notes") or fallback.get("notes")),
        })

    return jsonify({"shared": shared, "cars": out_cars})


@vagonweb_blueprint.route("/trainset", methods=["POST"])
@owner_required
def save_composition():
    """
    Save a parsed composition as a personal trainset.

    The running order comes from the page (repeats included, unlike the deduped
    catalogue view). Each cell is resolved to an already-imported wagon by its image
    path, which the importer owns; a cell split into cars expands to those cars in
    order. Vehicles that have not been imported are kept as placeholders under the
    name the importer would give them, so the formation is complete and heals itself
    on a later import. Always personal (is_admin false); public sets stay a
    deliberate act.

    Body: {name, cells: [{vw_base, code}, …]}
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    cells = data.get("cells") or []
    if not name:
        return jsonify({"error": "a name is required"}), 400
    if not cells:
        return jsonify({"error": "nothing to save — the composition is empty"}), 400

    username = session.get("logged_in")

    # Candidate image paths per cell, most specific first: the code's own entry, then
    # the drawing it was imported under.
    wanted: list[tuple[list[str], str, str]] = []
    for c in cells:
        base = str(c.get("vw_base") or "").strip()
        if not base:
            continue
        code = str(c.get("code") or "").strip()
        paths = [f"{VW_PREFIX}/{_variant_base(base, code)}"] if code else []
        paths.append(f"{VW_PREFIX}/{base}")
        wanted.append((paths, base, code))

    lookup = {p for paths, _, _ in wanted for p in paths}
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT name, image FROM wagons
            WHERE image = ANY(:paths) OR image LIKE ANY(:like_paths)
            ORDER BY image
            """,
            {
                "paths": list(lookup),
                "like_paths": [f"{p}\\_c%" for p in lookup],
            },
        ).fetchall()

        by_image: dict[str, str] = {}
        cars: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            by_image[r["image"]] = r["name"]
            head, sep, tail = r["image"].rpartition("_c")
            if sep and tail.isdigit():
                cars.setdefault(head, []).append((tail.zfill(4), r["name"]))

        units, missing = [], []
        for paths, base, code in wanted:
            for p in paths:
                if p in by_image:
                    units.append({"name": by_image[p], "_side": "L"})
                    break
                if p in cars:
                    units.extend({"name": n, "_side": "L"}
                                 for _, n in sorted(cars[p]))
                    break
            else:
                # Not imported yet — keep the vehicle in the formation rather than
                # dropping it, so the set still reads as the real train. The name is
                # the one the importer *would* give this code, so the placeholder
                # turns into the real wagon by itself once it is imported. Until
                # then the display finds no wagon and draws its placeholder art,
                # labelled with the type code.
                stand_in = _name_from_base(_variant_base(base, code) if code else base)
                units.append({
                    "name": stand_in,
                    "_side": "L",
                    "label": code or stand_in,
                })
                missing.append(code or stand_in)

        if pg.execute(
            "SELECT 1 FROM trainsets WHERE NOT is_admin AND username = :u AND name = :n",
            {"u": username, "n": name},
        ).fetchone():
            return jsonify({"error": f'you already have a trainset named "{name}"'}), 409

        trainset_id = pg.execute(
            """
            INSERT INTO trainsets (name, username, is_admin, units_json)
            VALUES (:name, :username, FALSE, :units_json)
            RETURNING id
            """,
            {"name": name, "username": username, "units_json": json.dumps(units)},
        ).scalar()

    return jsonify({
        "id": trainset_id, "name": name,
        "units": len(units),
        "missing": sorted(set(missing)),
    }), 201


@vagonweb_blueprint.route("/staging", methods=["DELETE"])
@owner_required
def clear_staging():
    removed = 0
    if STAGING_DIR.is_dir():
        for f in STAGING_DIR.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
                removed += 1
    return jsonify({"removed": removed})
