from imapclient import IMAPClient
import threading
import email as email_lib
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
import time
import logging
from datetime import datetime

from py.utils import load_config
from src.users import User
from src.utils import sendEmail, lang
from src.ai import parse_trip_with_ai, create_trip_from_parsed, extract_pdf_text, parse_ics_content

logger = logging.getLogger(__name__)
_app = None

def get_email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(errors="ignore")
            elif part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(errors="ignore")
    return ""

def extract_attachments(msg):
    attachments = {"ics": [], "pdf": []}
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        content_type = part.get_content_type()
        filename = part.get_filename()
        if content_type == "text/calendar" or (filename and filename.lower().endswith(".ics")):
            payload = part.get_payload(decode=True)
            if payload:
                attachments["ics"].append({"filename": filename, "data": payload})
        elif content_type == "application/pdf" or (filename and filename.lower().endswith(".pdf")):
            payload = part.get_payload(decode=True)
            if payload:
                attachments["pdf"].append({"filename": filename, "data": payload})
    return attachments

def get_user_from_sender(sender_raw):
    _, email_address = parseaddr(sender_raw)
    email_address = email_address.lower()
    user = User.query.filter_by(email=email_address).first()
    if not user:
        logger.info(f"No user found for email: {email_address}")
        try:
            sendEmail(email_address, "Trainlog - Email not recognized",
                """<h2>Email not recognized</h2><p>We received your email but could not find a Trainlog account associated with this address.</p><p><a href="https://trainlog.me">Visit Trainlog</a></p>""")
        except Exception as e:
            logger.error(f"Failed to send unrecognized email notice: {e}")
        return None
    if not user.premium:
        logger.info(f"User {user.username} is not premium")
        l = lang.get(user.lang, lang["en"])
        try:
            sendEmail(email_address, l["email_premium_subject"],
                f"""<h2>{l["email_premium_title"]}</h2><p>{l["email_premium_greeting"].format(username=user.username)}</p><p>{l["email_premium_description"]}</p><p><a href="https://buymeacoffee.com/trainlog/membership">{l["email_premium_cta"]}</a></p>""")
        except Exception as e:
            logger.error(f"Failed to send premium notice: {e}")
        return None
    return user

def get_original_email_date(msg):
    date_str = msg.get("Date")
    if date_str:
        try:
            return parsedate_to_datetime(date_str).date()
        except:
            pass
    return datetime.now().date()

def send_confirmation_email(user, created_trips, subject):
    trip_ids = ",".join(str(t.trip_id) for t in created_trips)
    trip_lines = [f"• {t.origin_station} → {t.destination_station} ({t.start_datetime.strftime('%Y-%m-%d') if t.start_datetime else '?'})" for t in created_trips]
    l = lang.get(user.lang, lang["en"])
    sendEmail(user.email, l["email_success_subject"],
        f"""<h2>{l["email_success_title"]}</h2><p>{l["email_received"]}: <strong>{subject}</strong></p><p><strong>{len(created_trips)} {l["email_trips_added"]}</strong></p><p>{"<br>".join(trip_lines)}</p><p><a href="https://trainlog.me/public/trip/{trip_ids}">{l["email_view_trips"]}</a></p>""")

def send_error_email(user, subject, error_message):
    l = lang.get(user.lang, lang["en"])
    try:
        sendEmail(user.email, l["email_error_subject"],
            f"""<h2>{l["email_error_title"]}</h2><p>{l["email_received"]}: <strong>{subject}</strong></p><p>{l["email_error_description"]}</p><p><em>{error_message}</em></p><p>{l["email_error_advice"]}</p>""")
    except Exception as e:
        logger.error(f"Failed to send error email: {e}")

def send_no_trips_email(user, subject):
    l = lang.get(user.lang, lang["en"])
    try:
        sendEmail(user.email, l["email_no_trips_subject"],
            f"""<h2>{l["email_no_trips_title"]}</h2><p>{l["email_received"]}: <strong>{subject}</strong></p><p>{l["email_no_trips_description"]}</p><p>{l["email_no_trips_formats"]}</p>""")
    except Exception as e:
        logger.error(f"Failed to send no-trips email: {e}")

def process_incoming_email(raw):
    msg = email_lib.message_from_bytes(raw)
    sender = msg["From"]
    
    with _app.app_context():
        user = get_user_from_sender(sender)
        if not user:
            return
        
        subject = "Unknown"
        try:
            subject_raw, enc = decode_header(msg["Subject"])[0]
            subject = subject_raw.decode(enc or "utf-8") if isinstance(subject_raw, bytes) else subject_raw
        except Exception as e:
            logger.error(f"Failed to decode subject: {e}")
        
        body = get_email_body(msg)
        purchase_date = get_original_email_date(msg)
        attachments = extract_attachments(msg)
        
        ics_events = []
        for att in attachments["ics"]:
            ics_events.extend(parse_ics_content(att["data"]))
        
        pdf_texts = []
        for att in attachments["pdf"]:
            text = extract_pdf_text(att["data"])
            if text.strip():
                pdf_texts.append(text)
        
        logger.info(f"Processing email from {user.username} (ICS: {len(ics_events)}, PDFs: {len(pdf_texts)})")
        
        try:
            trips = parse_trip_with_ai(f"Subject: {subject}\nBody: {body}", user.lang, ics_events=ics_events, pdf_texts=pdf_texts if pdf_texts else None)
        except Exception as e:
            logger.error(f"AI parsing failed: {e}")
            send_error_email(user, subject, "Failed to analyze the email content.")
            return
        
        if not trips:
            send_no_trips_email(user, subject)
            return
        
        created_trips, errors = [], []
        for i, parsed in enumerate(trips):
            try:
                trip = create_trip_from_parsed(user, parsed, purchase_date, source="email")
                if trip:
                    created_trips.append(trip)
                else:
                    errors.append(f"Trip {i+1}: Could not geocode")
            except Exception as e:
                logger.error(f"Failed to create trip {i+1}: {e}")
                errors.append(f"Trip {i+1}: {str(e)}")
        
        if created_trips:
            try:
                send_confirmation_email(user, created_trips, subject)
            except Exception as e:
                logger.error(f"Failed to send confirmation: {e}")
            if errors:
                send_error_email(user, subject, f"Created {len(created_trips)} trip(s), but some failed: " + "; ".join(errors))
        else:
            send_error_email(user, subject, "Could not create any trips. " + "; ".join(errors) if errors else "Unknown error.")

def email_listener():
    config = load_config()
    cfg = config.get("email_receiver")
    if not cfg:
        logger.warning("No email_receiver config found")
        return
    if cfg["enabled"]:
        while True:
            try:
                client = IMAPClient(cfg["imap"], ssl=True)
                client.login(cfg["user"], cfg["password"])
                client.select_folder("INBOX")
                logger.info("Email listener connected")
                
                while True:
                    client.idle()
                    responses = client.idle_check(timeout=300)
                    client.idle_done()
                    if responses:
                        for msg_id in client.search("UNSEEN"):
                            raw = client.fetch([msg_id], ["RFC822"])[msg_id][b"RFC822"]
                            process_incoming_email(raw)
            except Exception as e:
                logger.error(f"Email listener error: {e}")
                time.sleep(10)
    else: 
        logger.info("Email listener disabled")

def start_email_listener(app):
    global _app
    _app = app
    threading.Thread(target=email_listener, daemon=True).start()