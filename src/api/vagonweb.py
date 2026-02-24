"""
Vagonweb train composition fetcher.

    from vagonweb_compo import get_composition, vagonweb_blueprint

    wagons = get_composition("RJ 85")
    wagons = get_composition("RJ 85", date="2026-03-15")

Register the blueprint for the API:

    app.register_blueprint(vagonweb_blueprint)

    GET /api/composition?train=RJ+85
    GET /api/composition?train=RJ+85&date=2026-03-15
    GET /api/composition?train=RJ+85&format=visual
"""

import re
import json
import posixpath
from datetime import date as Date

import requests
from flask import Blueprint, request, jsonify

BASE = "https://www.vagonweb.cz/razeni"
IMG_BASE = "https://www.vagonweb.cz"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TrainCompoBot/1.0)",
    "Referer": f"{BASE}/vlak.php",
}

CLASS_MAP = {
    "tab-1tr": "1st",
    "tab-2tr": "2nd",
    "tab-2ptr": "2nd-plus",
    "tab-club": "business",
    "tab-jidel": "dining",
    "tab-luzk": "sleeper",
    "tab-lehk": "couchette",
    "tab-sluz": "service",
}

CLASS_REGEX = re.compile(r"class='(tab-(?:" + "|".join(k.split("-", 1)[1] for k in CLASS_MAP) + "))'")


def resolve_train(train: str, year: int) -> dict:
    resp = requests.get(
        f"{BASE}/json_vlaky.php",
        params={"jmeno": train.strip(), "rok": year},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = json.loads(resp.content.decode("utf-8-sig"))

    if not results:
        raise ValueError(f"Train '{train}' not found on vagonweb for year {year}")

    query_num = re.sub(r"[^0-9]", "", train)
    best = results[0]
    for r in results:
        if str(r.get("cislo", "")) == query_num:
            best = r
            break

    return {
        "operator": best["zeme"],
        "number": str(best["cislo"]),
        "name": best.get("nazev", best.get("name", "")),
    }


def get_composition(train: str, date: str | None = None, year: int | None = None) -> list[dict]:
    if year is None:
        year = int(date[:4]) if date else Date.today().year

    info = resolve_train(train, year)

    params = {
        "rok": str(year),
        "zeme": info["operator"],
        "cislo": info["number"],
        "nazev": info["name"],
        "styl": "r",
        "aktualni_rok": str(year),
        "cislo_vozu": "",
        "virtualni_vlak": "",
        "cislo_alias": "",
    }

    if date:
        params.update(od=date, do_x=date)
        html = _post(f"{BASE}/ajax_dalsi_razeni_vlak.php", params)
    else:
        cal_html = _post(f"{BASE}/ajax_kalendar_planovany.php", params)
        m = re.search(r"od='(\d{4}-\d{2}-\d{2})'\s+do='(\d{4}-\d{2}-\d{2})'", cal_html)
        params.update(od=m.group(1) if m else "", do_x=m.group(2) if m else "")
        html = _post(f"{BASE}/ajax_dalsi_razeni_vlak.php", params)

    return _parse_wagons(html)


# ── Internals ────────────────────────────────────────────────────────────────

def _post(url, data):
    resp = requests.post(url, data=data, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def _resolve_img_url(src):
    if src.startswith("http"):
        return src
    normalized = posixpath.normpath(src)
    if normalized.startswith(".."):
        return IMG_BASE + "/" + posixpath.normpath("razeni/" + normalized)
    if normalized.startswith("/"):
        return IMG_BASE + normalized
    return IMG_BASE + "/razeni/" + normalized


def _parse_wagons(html: str) -> list[dict]:
    chunks = re.split(r"<hr>", html)
    first_block = chunks[0] if chunks else html

    parts = re.split(r"<td class='bunka_vozu'", first_block)
    if len(parts) <= 1:
        return []

    wagons = []
    for cell in parts[1:]:
        im = re.search(
            r"<img\s+class='(?:obraceci )?obrazek_vagonu'\s+"
            r"style='height:(\d+)px;\s*width:(\d+)px;'\s+"
            r"src='([^']+)'",
            cell,
        )
        if not im:
            continue

        h, w, src = int(im.group(1)), int(im.group(2)), im.group(3)

        wagon = {
            "img_url": _resolve_img_url(src),
            "width": w,
            "height": h,
            "coach_no": None,
            "label": "",
            "classes": [],
        }

        cn = re.search(r"<span class=raz-cislo>(\d+)</span>", cell)
        if cn:
            wagon["coach_no"] = cn.group(1)

        lbl = []
        op = re.search(r">([A-ZÖÜČŽŠa-zöüčžš]{2,6})</span>\s*<span class=tab-radam>", cell)
        if op:
            lbl.append(op.group(1))
        typ = re.search(r"<span class=tab-radam>([^<]+)<sup>", cell)
        if typ:
            lbl.append(typ.group(1).strip())
        sub = re.search(r"</span>\s*<small>([^<]+)</small>", cell)
        if sub:
            lbl.append(sub.group(1).strip())
        wagon["label"] = " ".join(lbl)

        raw = set(CLASS_REGEX.findall(cell))
        wagon["classes"] = sorted(CLASS_MAP[c] for c in raw if c in CLASS_MAP)

        wagons.append(wagon)

    return wagons


# ── Blueprint ────────────────────────────────────────────────────────────────

vagonweb_blueprint = Blueprint("vagonweb", __name__)


@vagonweb_blueprint.route("/api/composition")
def api_composition():
    try:
        train = request.args.get("train")
        if not train:
            return jsonify({"error": "Missing 'train' parameter"}), 400
        wagons = get_composition(
            train=train,
            date=request.args.get("date"),
            year=int(request.args["year"]) if "year" in request.args else None,
        )
        if request.args.get("format") == "visual":
            imgs = "".join(f'<img src="{w["img_url"]}" height="{w["height"]}">' for w in wagons)
            return f'<div style="display:flex;align-items:flex-end">{imgs}</div>', 200, {"Content-Type": "text/html"}
        return jsonify(wagons)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500