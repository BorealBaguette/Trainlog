import logging

from flask import Blueprint, render_template, request, session

from py.utils import get_flag_emoji
from src.suspicious_activity import list_denied_logins, list_suspicious_activity
from src.utils import getUser, has_current_trip, lang, owner_required

logger = logging.getLogger(__name__)

admin_blueprint = Blueprint("admin", __name__)


@admin_blueprint.route("/denied_logins")
@owner_required
def denied_logins():
    denied_logins = list_denied_logins()
    denied_logins = [dict(login) for login in denied_logins]

    for login in denied_logins:
        login["ip_emoji"] = get_flag_emoji(login["ip_country"])

    return render_template(
        "admin/denied_logins.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        denied_logins=denied_logins,
        isCurrent=has_current_trip(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@admin_blueprint.route("/suspicious")
@owner_required
def suspicious_activity():
    limit = request.args.get("limit", "2000")
    if limit == "all":
        limit = None
    else:
        try:
            limit = int(limit)
        except ValueError:
            limit = 2000

    suspicious_activities_base = list_suspicious_activity(limit)
    suspicious_activities = []

    for activity in suspicious_activities_base:
        suspicious_activities.append(dict(activity))
        suspicious_activities[-1]["ip_emoji"] = get_flag_emoji(
            suspicious_activities[-1]["ip_country"]
        )

    return render_template(
        "admin/suspicious_activity.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        activities=suspicious_activities,
        isCurrent=has_current_trip(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )
