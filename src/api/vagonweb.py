"""
VagonWeb page importer.

Fetches a VagonWeb train composition page, extracts all wagon types and
compositions, downloads the GIF images, and imports everything into the DB.

Endpoint:
    POST /api/admin/vagonweb/import
    Body: {"url": "https://www.vagonweb.cz/razeni/vlak.php?..."}
"""

import html as html_mod
import json
import re
import posixpath
from pathlib import Path

import requests
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from flask import Blueprint, jsonify, request, session

from src.pg import pg_session
from src.utils import admin_required

# ── Constants ─────────────────────────────────────────────────────────────────

BASE        = "https://www.vagonweb.cz"
VW_IMG_BASE = BASE + "/popisy/img/"
HEADERS     = {
    "User-Agent": "Mozilla/5.0 (compatible; TrainCompoBot/1.0)",
    "Referer":    BASE + "/razeni/vlak.php",
}

WAGONS_ROOT = Path("static/images/wagons").resolve()
VW_PREFIX   = "images/vagonweb"

CLASS_MAP = {
    "tab-1tr":   "1st",
    "tab-2tr":   "2nd",
    "tab-2ptr":  "2nd-plus",
    "tab-club":  "business",
    "tab-jidel": "dining",
    "tab-luzk":  "sleeper",
    "tab-lehat": "couchette",
    "tab-sluz":  "service",
}

_CLASS_PAT = re.compile(
    r"<td class='(tab-(?:" + "|".join(k.split("-", 1)[1] for k in CLASS_MAP) + "))'>"
)

# ── Image helpers ──────────────────────────────────────────────────────────────

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
    path = url[len(VW_IMG_BASE):]          # "ZSSK/Bpeer-2970-B-a.gif"
    path = re.sub(r"-[ab]\.gif$", "", path, flags=re.IGNORECASE)
    return path or None


def _name_from_base(base: str) -> str:
    """
    Sanitise a local image base path to a valid DB primary key.

    "ZSSK/Bpeer-2970-B"  →  "ZSSK_Bpeer_2970_B"
    """
    return re.sub(r"[^\w]+", "_", base).strip("_") or "wagon"


def _download_gif(url: str, dest: Path) -> bool:
    """Download a GIF from VagonWeb and save it locally. Returns True on success."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return False
        content = resp.content
        if content[:6] not in (b"GIF87a", b"GIF89a"):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return True
    except Exception:
        return False


# ── HTML parsing ───────────────────────────────────────────────────────────────

def _strip_tags(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def _parse_train_title(html: str) -> str:
    """
    Extract a clean train title, trying three sources in order:

    1. velky15 span — IC/EC pages with multiple carriers:
       <span class='velky15'><img alt='IC'> 186 <i>Hernád</i> …</span>  →  "IC 186 Hernád"

    2. Plain <h2> — single-carrier pages:
       <h2><span title='Rt'>Rt </span> 606 <i>F4</i></h2>  →  "Rt 606 F4"

    3. Last segment of the <title> tag:
       "vagonWEB » … » Rt 606 F4"  →  "Rt 606 F4"
    """
    def _from_content(content: str, cat_from_img: bool) -> str:
        if cat_from_img:
            cat_m = re.search(r"<img[^>]+alt='([^']+)'", content)
            category = cat_m.group(1).strip() if cat_m else ""
        else:
            cat_m = re.search(r"<span[^>]+title='([^']+)'", content)
            category = cat_m.group(1).strip() if cat_m else ""
        name_m = re.search(r"<i>([^<]+)</i>", content)
        name   = name_m.group(1).strip() if name_m else ""
        num_m  = re.search(r"\b(\d+)\b", _strip_tags(content))
        number = num_m.group(1) if num_m else ""
        parts  = [p for p in [category, number, name] if p]
        return " ".join(parts)

    m = re.search(r"<span[^>]+class='velky15'>(.*?)</span>", html, re.DOTALL)
    if m:
        title = _from_content(m.group(1), cat_from_img=True)
        if title:
            return title

    m = re.search(r"<h2[^>]*>(.*?)</h2>", html, re.DOTALL)
    if m:
        title = _from_content(m.group(1), cat_from_img=False)
        if title:
            return title

    # Last fallback: strip the verbose breadcrumb prefix from <title>
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        raw = html_mod.unescape(m.group(1))
        parts = [p.strip() for p in raw.split("»")]
        return parts[-1] if parts else raw.strip()

    return "Unknown Train"


def _clean_comp_title(h4_html: str) -> str:
    """
    Turn the verbose <h4> text into a short composition identifier.

    Scheduled composition <b>5.1.2026</b> - <b>12.12.2026</b>
        → "5.1.2026 – 12.12.2026"
    Real composition on: <b>Mon 23.2.2026</b> … <b>Miskolc-Tiszai</b>
        → "Mon 23.2.2026 · Miskolc-Tiszai"
    """
    bold_parts = re.findall(r"<b>([^<]+)</b>", h4_html)
    if not bold_parts:
        return re.sub(r"\s+", " ", _strip_tags(h4_html)).strip()
    if len(bold_parts) == 2 and "Scheduled" in h4_html:
        return f"{bold_parts[0]} – {bold_parts[1]}"
    return " · ".join(bold_parts)


def _parse_amenities(section: str) -> str:
    """
    Extract a human-readable amenity string from the tab-pocmist span.

    Returns e.g. "1st class, open coach / seating capacity: 52, air-conditioned,
                  230 V socket, USB socket, Wi-Fi, vacuum toilet"
    """
    m = re.search(r"<span class='tab-pocmist'>(.*?)</span>", section, re.DOTALL)
    if not m:
        return ""
    content = m.group(1)
    items = []
    for chunk in re.split(r"<img\b", content):
        title_m = re.search(r"\btitle='([^']+)'", chunk)
        if not title_m:
            continue
        title = title_m.group(1)
        # text after the closing > of this img tag — may carry a capacity number
        after = re.sub(r"^[^>]*>", "", chunk)
        num_m = re.search(r"([\d+]+)", after.strip())
        items.append(f"{title}: {num_m.group(1)}" if num_m else title)
    return ", ".join(items)


def _parse_description_section(section: str) -> dict:
    """
    Parse one description section (between <hr> tags or the whole info div).
    Returns: operator, wagon_type, sub_variant, coach_no, route, amenities.
    """
    cn = re.search(r"<span class=raz-cislo>(\d+)</span>", section)
    coach_no = cn.group(1) if cn else None

    op = re.search(
        r"<span[^>]+title='[^']*'[^>]*>([A-ZÖÜČŽŠÁÉÍÓÚÀÈÌÒÙ]{2,6})</span>\s*"
        r"<span class=tab-radam>",
        section,
    )
    operator = op.group(1) if op else ""

    wt = re.search(r"<span class=tab-radam>([^<]+)<sup>", section)
    wagon_type = wt.group(1).strip() if wt else ""

    sv = re.search(r"<small>([^<]*)</small>", section)
    sub_variant = sv.group(1).strip() if sv else ""

    rt = re.search(r"<b>([^<]+)</b>", section)
    route = rt.group(1).strip() if rt else ""

    amenities = _parse_amenities(section)

    return {
        "operator":    operator,
        "wagon_type":  wagon_type,
        "sub_variant": sub_variant,
        "coach_no":    coach_no,
        "route":       route,
        "amenities":   amenities,
    }


def _parse_cell(cell: str, vlak_id: str, cell_idx: int, full_html: str) -> list[dict]:
    """
    Parse one <td class='bunka_vozu'> cell.
    Returns a list of wagon dicts (usually 1, but 2+ for multi-loco animated cells).
    """
    # --- classes from the class-bar header rows ---
    raw_classes = set(_CLASS_PAT.findall(cell))
    classes = sorted(CLASS_MAP[c] for c in raw_classes if c in CLASS_MAP)

    # --- primary image ---
    im = re.search(
        r"<img class='((?:obraceci )?obrazek_vagonu)'([^>]*)src='([^']+)'",
        cell,
    )
    if not im:
        return []

    class_attr = im.group(1)
    attr_rest  = im.group(2)
    img_src    = im.group(3)
    img_url_a  = _resolve_img_url(img_src)
    has_both   = "obraceci" in class_attr

    # --- check for JS-animated multi-image cell ---
    is_animated = bool(re.search(r"id='obraz_", attr_rest + im.group(2)))
    # also check by looking for id="obraz_{vlak_id}_{cell_idx}"
    anim_id = f"obraz_{vlak_id}_{cell_idx}"
    if anim_id in cell:
        is_animated = True

    # --- gather images: either single (+ optional flip) or JS array ---
    images = []  # list of (img_url_a, img_url_b, has_both_sides)

    if is_animated:
        # Parse the JS array  obr_VLAKID_CELLNUM[N].src = '...'
        js_pat = re.compile(
            rf"obr_{re.escape(vlak_id)}_{re.escape(str(cell_idx))}\[\d+\]\.src='([^']+)'"
        )
        js_srcs = js_pat.findall(full_html)
        if js_srcs:
            for src in js_srcs:
                url_a = _resolve_img_url(src)
                images.append((url_a, None, False))
        else:
            images.append((img_url_a, None, False))
    else:
        img_url_b = img_url_a.replace("-a.gif", "-b.gif") if has_both else None
        images.append((img_url_a, img_url_b, has_both))

    # --- description sections (split by <hr>) ---
    # The info div starts after the image <table>
    info_match = re.search(r"</table>(.*)", cell, re.DOTALL)
    info_block = info_match.group(1) if info_match else cell
    sections   = re.split(r"<hr>", info_block)

    # Build one wagon dict per image, matched with description sections
    wagons = []
    for i, (url_a, url_b, both) in enumerate(images):
        desc = _parse_description_section(sections[i] if i < len(sections) else sections[-1])
        wagons.append({
            "img_url_a":      url_a,
            "img_url_b":      url_b,
            "has_both_sides": both,
            "operator":       desc["operator"],
            "wagon_type":     desc["wagon_type"],
            "sub_variant":    desc["sub_variant"],
            "coach_no":       desc["coach_no"],
            "classes":        classes,
            "route":          desc["route"],
            "amenities":      desc["amenities"],
        })
    return wagons


def _parse_page(html: str) -> dict:
    """
    Parse a full VagonWeb train page.

    Returns:
        {
          "train_title": str,
          "compositions": [
            {
              "vlak_id": str,
              "title":   str,
              "wagons":  [ { img_url_a, img_url_b, has_both_sides,
                             operator, wagon_type, sub_variant,
                             coach_no, classes, route }, … ]
            }, …
          ]
        }
    """
    train_title = _parse_train_title(html)

    # Split into blocks starting at each vlak_id div
    # We look for  <div id='vlak_XXXXX' …>
    block_splits = list(re.finditer(r"<div id='vlak_(\d+)'", html))
    if not block_splits:
        return {"train_title": train_title, "compositions": []}

    compositions = []
    for idx, m in enumerate(block_splits):
        vlak_id = m.group(1)
        start   = m.start()
        end     = block_splits[idx + 1].start() if idx + 1 < len(block_splits) else len(html)
        block   = html[start:end]

        # Title from the <h4> tag
        h4 = re.search(r"<h4[^>]*>(.*?)</h4>", block, re.DOTALL)
        title = _clean_comp_title(h4.group(1)) if h4 else f"Composition {vlak_id}"

        # The vlacek table inside this block
        tbl_m = re.search(r"<table class='vlacek'[^>]*>(.*)", block, re.DOTALL)
        tbl   = tbl_m.group(1) if tbl_m else block

        # Split into bunka_vozu cells; index 0 is before the first cell → skip
        parts  = re.split(r"<td class='bunka_vozu[^']*'", tbl)
        wagons = []
        for cell_idx, cell in enumerate(parts[1:], start=1):
            wagons.extend(_parse_cell(cell, vlak_id, cell_idx, block))

        compositions.append({"vlak_id": vlak_id, "title": title, "wagons": wagons})

    return {"train_title": train_title, "compositions": compositions}


# ── Import orchestration ───────────────────────────────────────────────────────

def _ensure_english(url: str) -> str:
    """Add or override lang=en on a VagonWeb URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["lang"] = ["en"]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def import_from_url(url: str, admin_username: str) -> dict:
    """
    Fetch a VagonWeb page, import all wagons and compositions into the DB.

    Returns a summary dict:
        { wagons_imported, wagons_skipped, trainsets_created, errors }
    """
    resp = requests.get(_ensure_english(url), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    parsed = _parse_page(html)
    train_title = parsed["train_title"]

    wagons_imported  = 0
    wagons_skipped   = 0
    trainsets_created = 0
    errors: list[str] = []

    # Collect unique wagons across all compositions (keyed by local image base)
    unique: dict[str, dict] = {}
    for comp in parsed["compositions"]:
        for w in comp["wagons"]:
            base = _img_local_base(w["img_url_a"])
            if base and base not in unique:
                unique[base] = w

    # ── Upsert wagons ──────────────────────────────────────────────────────────
    with pg_session() as pg:
        for base, w in unique.items():
            name        = _name_from_base(base)
            image_field = f"{VW_PREFIX}/{base}"

            # Build display fields
            parts = [w["operator"], w["wagon_type"]]
            if w["sub_variant"]:
                parts.append(w["sub_variant"])
            nom        = " ".join(p for p in parts if p)
            titre1     = w["wagon_type"] or None
            titre2     = w["sub_variant"] or None
            notes      = w["amenities"] or ", ".join(w["classes"]) or None
            image_type = "sides" if w["has_both_sides"] else "sides_L"

            # Download images.
            # Convention (matching the admin upload handler and imgSrc):
            #   sides   → image_L.gif + image_R.gif
            #   sides_L → image.gif   (single view, no suffix)
            local_base_path = WAGONS_ROOT / VW_PREFIX / base
            if w["has_both_sides"]:
                dl_l = _download_gif(
                    w["img_url_a"],
                    Path(str(local_base_path) + "_L.gif"),
                )
            else:
                dl_l = _download_gif(
                    w["img_url_a"],
                    Path(str(local_base_path) + ".gif"),
                )
            if not dl_l:
                errors.append(f"Could not download {w['img_url_a']}")

            if w["has_both_sides"] and w["img_url_b"]:
                dl_r = _download_gif(
                    w["img_url_b"],
                    Path(str(local_base_path) + "_R.gif"),
                )
                if not dl_r:
                    errors.append(f"Could not download {w['img_url_b']}")

            # Upsert into wagons table
            result = pg.execute(
                """
                INSERT INTO wagons
                    (name, nom, titre1, titre2, epo, source, image, image_type, notes)
                VALUES
                    (:name, :nom, :titre1, :titre2, :epo, :source, :image, :image_type, :notes)
                ON CONFLICT (name) DO NOTHING
                RETURNING name
                """,
                {
                    "name":       name,
                    "nom":        nom,
                    "titre1":     titre1,
                    "titre2":     titre2,
                    "epo":        "6",
                    "source":     "VagonWeb",
                    "image":      image_field,
                    "image_type": image_type,
                    "notes":      notes,
                },
            )
            if result.fetchone():
                wagons_imported += 1
            else:
                wagons_skipped += 1

        # ── Create trainsets ───────────────────────────────────────────────────
        for comp in parsed["compositions"]:
            ts_name = f"{train_title} · {comp['title']}"

            units = []
            for w in comp["wagons"]:
                base = _img_local_base(w["img_url_a"])
                if base:
                    units.append({"name": _name_from_base(base), "_side": "L"})

            if not units:
                continue

            pg.execute(
                """
                INSERT INTO trainsets (name, username, is_admin, units_json)
                VALUES (:name, :username, :is_admin, :units_json)
                """,
                {
                    "name":       ts_name,
                    "username":   admin_username,
                    "is_admin":   False,
                    "units_json": json.dumps(units),
                },
            )
            trainsets_created += 1

    return {
        "wagons_imported":   wagons_imported,
        "wagons_skipped":    wagons_skipped,
        "trainsets_created": trainsets_created,
        "errors":            errors,
    }


# ── Blueprint ──────────────────────────────────────────────────────────────────

vagonweb_blueprint = Blueprint("vagonweb", __name__)


@vagonweb_blueprint.route("/api/admin/vagonweb/import", methods=["POST"])
@admin_required
def api_vagonweb_import():
    data = request.get_json(force=True) or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        summary = import_from_url(url, session.get("logged_in", "admin"))
        return jsonify(summary)
    except requests.HTTPError as e:
        return jsonify({"error": f"HTTP error fetching page: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500
