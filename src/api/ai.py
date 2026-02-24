import base64
import logging
import uuid

from flask import Blueprint, jsonify, render_template, request, session

from src.ai import (
    create_trip_from_parsed,
    enrich_parsed_trip,
    extract_pdf_text,
    parse_ics_content,
    parse_trip_with_ai,
)
from src.users import User
from src.utils import ai_usage, check_and_increment_ai_usage, lang, login_required

logger = logging.getLogger(__name__)
ai_blueprint = Blueprint("ai", __name__)
_pending_trips = {}


@ai_blueprint.route("/u/<username>/new/ai", methods=["GET"])
@login_required
def new_trip_ai(username):
    user = User.query.filter_by(username=username).first()
    language = lang.get(user.lang, lang["en"])
    return render_template(
        "new_trip_ai.html",
        username=username,
        l=language,
        ai_calls=ai_usage(username),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@ai_blueprint.route("/u/<username>/new/ai/parse", methods=["POST"])
@login_required
def parse_trip_ai(username):
    user = User.query.filter_by(username=username).first()

    text = request.form.get("text", "").strip()
    files = request.files.getlist("files")

    if not text and not files:
        return jsonify({"error": "Provide text, image, or file"}), 400

    pdf_texts = []
    ics_events = []
    csv_texts = []
    images = []  # Changed to list

    for f in files:
        if not f or not f.filename:
            continue

        filename = f.filename.lower()
        data = f.read()

        if filename.endswith(".pdf"):
            pdf_text = extract_pdf_text(data)
            if pdf_text.strip():
                pdf_texts.append(pdf_text)

        elif filename.endswith(".ics"):
            events = parse_ics_content(data)
            ics_events.extend(events)

        elif filename.endswith(".csv"):
            try:
                csv_texts.append(data.decode("utf-8", errors="ignore"))
            except UnicodeDecodeError:
                csv_texts.append(data.decode("latin-1", errors="ignore"))

        elif filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            image_data = base64.b64encode(data).decode("utf-8")
            ext = filename.rsplit(".", 1)[-1]
            mime_map = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "gif": "image/gif",
                "webp": "image/webp",
            }
            image_mime = mime_map.get(ext, "image/png")
            images.append({"data": image_data, "mime": image_mime})

    if csv_texts:
        text += "\n\nCSV DATA:\n" + "\n---\n".join(csv_texts)

    try:
        trips = parse_trip_with_ai(
            text,
            user.lang,
            images=images if images else None,  # Pass list of images
            ics_events=ics_events if ics_events else None,
            pdf_texts=pdf_texts if pdf_texts else None,
        )
    except Exception as e:
        logger.error(f"AI parsing failed: {e}")
        return jsonify({"error": "Failed to parse"}), 500

    if not trips:
        return jsonify({"error": "No trips found"}), 400

    enriched_trips = []
    for trip in trips:
        enriched = enrich_parsed_trip(trip)
        if enriched:
            enriched_trips.append(enriched)
        else:
            trip["_enrich_failed"] = True
            enriched_trips.append(trip)

    parse_id = str(uuid.uuid4())

    return jsonify({"parse_id": parse_id, "trips": enriched_trips})


@ai_blueprint.route("/u/<username>/new/ai/save", methods=["POST"])
@login_required
def save_trip_ai(username):
    user = User.query.filter_by(username=username).first()

    data = request.get_json(silent=True) or {}

    # NEW: selected trip objects are sent by the frontend
    selected_trips = data.get("trips", [])
    if not isinstance(selected_trips, list) or not selected_trips:
        return jsonify({"error": "No trips provided"}), 400

    # Filter out any invalid / failed enrich payloads
    valid_trips = [
        t
        for t in selected_trips
        if isinstance(t, dict)
        and t.get("origin")
        and t.get("destination")
        and not t.get("_enrich_failed")
    ]
    if not valid_trips:
        return jsonify({"error": "No valid trips to save"}), 400

    # Check usage limit before saving (count valid trips only)
    if not check_and_increment_ai_usage(username, count=len(valid_trips)):
        return jsonify(
            {
                "error": "Monthly limit reached (10 trips). Upgrade to premium for unlimited."
            }
        ), 403

    created = []
    for trip_data in valid_trips:
        try:
            trip = create_trip_from_parsed(user, trip_data, source="ai")
            if trip:
                created.append(
                    {
                        "id": trip.trip_id,
                        "origin": trip.origin_station,
                        "destination": trip.destination_station,
                    }
                )
        except Exception as e:
            logger.error(f"Failed to create trip: {e}")

    return jsonify({"count": len(created), "trips": created})


@ai_blueprint.route("/u/<username>/new/ai/cancel", methods=["POST"])
@login_required
def cancel_trip_ai(username):
    return jsonify({"ok": True})
