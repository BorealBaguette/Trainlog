"""What a shared trip page shows besides the trips themselves: prices and CO₂.

Most people who open a shared link have no account, so only the link can say
what they may see:

- A plain link follows the owner's account settings (User.share_price /
  User.share_carbon). That is what the trips table's "View on map" opens and its
  ▲ menu copies by default, so the usual share costs no extra click.
- For the exception, the trips table's ▲ menu has toggles (preset to those
  settings) and a copy button; a choice other than the settings is baked into a
  share key of its own (trip_selections.share_flags). Nothing in the URL can be
  edited or removed to change what it shows: a different choice is a different key.
- Tags work the same way from the tag list: the plain tag link follows the settings
  and any other choice is a key of its own, which still follows the tag's trips
  (trip_selections.tag_uuid).

The page then signs what it settled on for /getPublicTrips, which leaves the
fields out of the JSON for anyone who does not own the trips unless that
signature allows them: hiding them is not just a matter of display.
"""

import base64
import hashlib
import hmac

from flask import current_app

FLAGS = ("price", "carbon")
_CODES = {"price": "p", "carbon": "c"}

# Fields that give a price away, as processPublicTrips hands them out.
PRICE_FIELDS = (
    "price",
    "currency",
    "purchasing_date",
    "price_in_user_currency",
    "user_currency",
    "ticket_ids",
    "ticket",
    "ticket_count",
    "ticket_price",
    "ticket_currency",
    "ticket_price_in_user_currency",
)
CARBON_FIELDS = ("carbon", "co2_override", "carbon_footprint")

NO_FLAGS = {flag: False for flag in FLAGS}


def encode(flags):
    """'p', 'c', 'pc' or 'n': how flags are stored in trip_selections.share_flags."""
    return "".join(_CODES[flag] for flag in FLAGS if flags.get(flag)) or "n"


def decode(code):
    return {flag: _CODES[flag] in code for flag in FLAGS}


def owner_defaults(owners):
    """What a plain link shows. With several owners (only the site owner can open
    such a page) a field shows only when every one of them has it on."""
    owners = [owner for owner in owners if owner is not None]
    return {
        flag: bool(owners) and all(bool(getattr(o, f"share_{flag}", False)) for o in owners)
        for flag in FLAGS
    }


def page_flags(key_flags, args, owners, viewer_owns):
    """What this page view shows: the choice baked into its share key first
    (`key_flags`, None for a plain key or link), then the owner's settings.

    The old `?price` / `?carbon` switches are honoured for the owner only, for
    links they kept. For anyone else they did nothing but reveal what the JSON
    already held, which is exactly what this replaces. (A ticket's own page always
    shows prices: render_public_trip_page sets that itself.)
    """
    if key_flags:
        return decode(key_flags)
    if viewer_owns and any(flag in args for flag in FLAGS):
        return {flag: flag in args for flag in FLAGS}
    return owner_defaults(owners)


def _signature(subject, code):
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    digest = hmac.new(key, f"view|{subject}|{code}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:9]).decode()


def sign_view(trip_ids, flags):
    """Token the page sends to /getPublicTrips with the same `trip_ids` string."""
    code = encode(flags)
    return f"{code}.{_signature(trip_ids, code)}"


def read_view(trip_ids, token):
    """The flags a /getPublicTrips request may have; nothing for a bad token."""
    if not token or "." not in token:
        return dict(NO_FLAGS)
    code, _, signature = token.partition(".")
    if not code or set(code) - set(_CODES.values()) - {"n"}:
        return dict(NO_FLAGS)
    if not hmac.compare_digest(signature, _signature(trip_ids, code)):
        return dict(NO_FLAGS)
    return decode(code)


def strip_trip(trip, flags):
    """Remove from a trip dict what `flags` does not allow. Returns which kinds
    were removed, so the caller can blank the matching totals."""
    removed = set()
    if not flags.get("price"):
        for field in PRICE_FIELDS:
            trip.pop(field, None)
        removed.add("price")
    if not flags.get("carbon"):
        for field in CARBON_FIELDS:
            trip.pop(field, None)
        removed.add("carbon")
    return removed
