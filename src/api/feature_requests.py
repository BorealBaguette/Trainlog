import logging

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.pg import pg_session
from src.sql import feature_requests as fr_sql
from src.users import User
from src.utils import (
    get_user_id,
    has_current_trip,
    lang,
    owner,
    owner_required,
    post_to_discord,
    sendEmailToUser,
)

logger = logging.getLogger(__name__)

feature_requests_blueprint = Blueprint("feature_requests", __name__)


@feature_requests_blueprint.route("/feature_requests")
def feature_requests(username=None):
    """Display feature requests page, with voting if user is logged in"""
    userinfo = session.get("userinfo", {})
    current_user = userinfo.get("logged_in_user")

    # Get sort parameter (default to score)
    sort_by = request.args.get("sort", "score")

    with pg_session() as pg:
        if current_user:
            # Get requests with user's votes
            if sort_by == "date":
                result = pg.execute(
                    fr_sql.list_feature_requests_with_votes_by_date(),
                    {"username": current_user},
                ).fetchall()
            else:
                result = pg.execute(
                    fr_sql.list_feature_requests_with_votes(),
                    {"username": current_user},
                ).fetchall()
        else:
            # Get requests without user votes
            if sort_by == "date":
                result = pg.execute(fr_sql.list_feature_requests_by_date()).fetchall()
            else:
                result = pg.execute(fr_sql.list_feature_requests()).fetchall()

        # Convert to list of dictionaries
        request_list = []
        for req in result:
            if req[3] == owner:
                author_display = "admin"
            else:
                author_display = req[3]
            request_dict = {
                "id": req[0],
                "title": req[1],
                "description": req[2],
                "author_display": author_display,
                "status": req[4],
                "created": req[5],
                "upvotes": req[6],
                "downvotes": req[7],
                "score": req[8],
                "user_vote": req[9] if len(req) > 9 else 0,
                "closure_reason": req[10] if len(req) > 10 else None,
            }
            request_list.append(request_dict)

    return render_template(
        "feature_requests.html",
        username=current_user,
        requests=request_list,
        current_sort=sort_by,
        **lang.get(userinfo.get("lang", "en"), {}),
        **userinfo,
        nav="bootstrap/navigation.html"
        if current_user != "public"
        else "bootstrap/no_user_nav.html",
        isCurrent=has_current_trip(),
    )


@feature_requests_blueprint.route("/feature_requests/<int:request_id>")
def single_feature_request(request_id):
    """Display a single feature request page"""
    userinfo = session.get("userinfo", {})
    current_user = userinfo.get("logged_in_user")

    with pg_session() as pg:
        if current_user:
            # Get request with user's vote
            result = pg.execute(
                fr_sql.get_single_feature_request_with_vote(),
                {"request_id": request_id, "username": current_user},
            ).fetchone()
        else:
            # Get request without user vote
            result = pg.execute(
                fr_sql.get_single_feature_request(), {"request_id": request_id}
            ).fetchone()

        if not result:
            return render_template("404.html"), 404

        # Convert to dictionary
        if result[3] == owner:
            author_display = "admin"
        else:
            author_display = result[3]

        request_dict = {
            "id": result[0],
            "title": result[1],
            "description": result[2],
            "author_display": author_display,
            "status": result[4],
            "created": result[5],
            "upvotes": result[6],
            "downvotes": result[7],
            "score": result[8],
            "user_vote": result[9] if len(result) > 9 else 0,
            "closure_reason": result[10] if len(result) > 10 else None,
        }

    return render_template(
        "single_feature_request.html",
        username=current_user,
        request=request_dict,
        **lang.get(userinfo.get("lang", "en"), {}),
        **userinfo,
        nav="bootstrap/navigation.html"
        if current_user != "public"
        else "bootstrap/no_user_nav.html",
        isCurrent=has_current_trip(),
    )


def login_required(f):
    """Decorator to require login - implement according to your auth system"""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("userinfo", {}).get("logged_in_user"):
            return redirect(url_for("feature_requests"))
        return f(*args, **kwargs)

    return decorated_function


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/submit", methods=["POST"]
)
@login_required
def submit_feature_request(username):
    """Submit a new feature request"""
    title = request.form["title"]
    description = request.form["description"]
    current_user = session["userinfo"]["logged_in_user"]
    is_owner = session["userinfo"].get("is_owner", False)
    display_name = current_user if not is_owner else "admin"
    with pg_session() as pg:
        result = pg.execute(
            fr_sql.insert_feature_request(),
            {"title": title, "description": description, "username": current_user},
        ).fetchone()

        # Redirect to the new feature request page
        if result:
            new_id = result[0]

            # Post to Discord
            post_to_discord(
                webhook_type="feature_requests",
                title="💡 New Feature Request",
                description=f"**{title}**\n\n{description}",
                url=url_for(
                    "feature_requests.single_feature_request",
                    request_id=new_id,
                    _external=True,
                ),
                fields=[
                    {"name": "Submitted by", "value": display_name, "inline": True},
                    {"name": "Request ID", "value": f"#{new_id}", "inline": True},
                ],
                footer_text="Feature Requests",
            )

            return redirect(
                url_for("feature_requests.single_feature_request", request_id=new_id)
            )

    return redirect(url_for("feature_requests.feature_requests"))


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/edit", methods=["POST"]
)
@login_required
def edit_feature_request(username):
    """Edit a feature request (owner can edit any, users can edit their own)"""
    request_id = request.form["request_id"]
    title = request.form["title"]
    description = request.form["description"]
    current_user = session["userinfo"]["logged_in_user"]
    is_owner = session["userinfo"].get("is_owner", False)

    with pg_session() as pg:
        # Check if user can edit this request
        if not is_owner:
            # Regular user can only edit their own requests
            result = pg.execute(
                fr_sql.get_feature_request_author(), {"request_id": request_id}
            ).fetchone()

            if not result or result[0] != current_user:
                logger.warning(
                    f"User {current_user} attempted to edit request {request_id} they don't own"
                )
                return redirect(url_for("feature_requests.feature_requests"))

        # Update the request
        pg.execute(
            fr_sql.update_feature_request(),
            {"request_id": request_id, "title": title, "description": description},
        )

    return redirect(
        url_for("feature_requests.single_feature_request", request_id=request_id)
    )


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/delete", methods=["POST"]
)
@login_required
def delete_feature_request(username):
    """Delete a feature request (owner can delete any, users can delete their own)"""
    request_id = request.form["request_id"]
    current_user = session["userinfo"]["logged_in_user"]
    is_owner = session["userinfo"].get("is_owner", False)

    with pg_session() as pg:
        # Check if user can delete this request
        if not is_owner:
            # Regular user can only delete their own requests
            result = pg.execute(
                fr_sql.get_feature_request_author(), {"request_id": request_id}
            ).fetchone()

            if not result or result[0] != current_user:
                logger.warning(
                    f"User {current_user} attempted to delete request {request_id} they don't own"
                )
                return redirect(url_for("feature_requests.feature_requests"))

        # Delete associated votes first
        pg.execute(fr_sql.delete_all_votes_for_request(), {"request_id": request_id})

        # Delete the request
        pg.execute(fr_sql.delete_feature_request(), {"request_id": request_id})

    return redirect(url_for("feature_requests.feature_requests"))


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/vote", methods=["POST"]
)
@login_required
def vote_feature_request(username):
    """Handle upvote/downvote for feature requests"""
    # Prevent owner from voting
    if session["userinfo"]["is_owner"]:
        return redirect(url_for("feature_requests.feature_requests"))

    request_id = request.form.get("request_id")
    vote_type = request.form.get("vote_type")
    current_user = session["userinfo"]["logged_in_user"]

    # Validate inputs
    if not request_id or not vote_type:
        logger.error(f"Missing request_id ({request_id}) or vote_type ({vote_type})")
        return redirect(url_for("feature_requests.feature_requests"))

    try:
        request_id = int(request_id)
    except (ValueError, TypeError):
        logger.error(f"Invalid request_id: {request_id}")
        return redirect(url_for("feature_requests.feature_requests"))

    if vote_type not in ["upvote", "downvote"]:
        logger.error(f"Invalid vote_type: {vote_type}")
        return redirect(url_for("feature_requests.feature_requests"))

    with pg_session() as pg:
        # Check if user has already voted on this request
        existing_vote_result = pg.execute(
            fr_sql.get_user_vote(), {"request_id": request_id, "username": current_user}
        ).fetchone()

        existing_vote = existing_vote_result[0] if existing_vote_result else None

        if existing_vote:
            if existing_vote == vote_type:
                # User is clicking the same vote - remove it
                pg.execute(
                    fr_sql.delete_vote(),
                    {"request_id": request_id, "username": current_user},
                )
            else:
                # User is changing their vote
                pg.execute(
                    fr_sql.update_vote(),
                    {
                        "request_id": request_id,
                        "username": current_user,
                        "vote_type": vote_type,
                    },
                )
        else:
            # New vote
            pg.execute(
                fr_sql.insert_vote(),
                {
                    "request_id": request_id,
                    "username": current_user,
                    "vote_type": vote_type,
                },
            )

        # Update vote counts in feature_requests table
        pg.execute(fr_sql.update_vote_counts(), {"request_id": request_id})

    # Check if we came from single request page
    referer = request.headers.get("Referer", "")
    if f"/feature_requests/{request_id}" in referer:
        return redirect(
            url_for("feature_requests.single_feature_request", request_id=request_id)
        )

    return redirect(url_for("feature_requests.feature_requests"))


@feature_requests_blueprint.route("/feature_requests/<int:request_id>/voters")
def feature_request_voters(request_id):
    """Get list of voters for a feature request"""
    with pg_session() as pg:
        result = pg.execute(fr_sql.list_voters(), {"request_id": request_id}).fetchall()

        voters = {"upvoters": [], "downvoters": []}

        for vote in result:
            vote_data = {
                "username": vote[0],
                "created": vote[2].isoformat() if vote[2] else None,
            }

            if vote[1] == "upvote":
                voters["upvoters"].append(vote_data)
            else:
                voters["downvoters"].append(vote_data)

    return jsonify(voters)


@feature_requests_blueprint.route("/feature_requests/<int:request_id>/voters")
def public_feature_request_voters(request_id):
    """Get list of voters for a feature request (public route)"""
    with pg_session() as pg:
        result = pg.execute(fr_sql.list_voters(), {"request_id": request_id}).fetchall()

        voters = {"upvoters": [], "downvoters": []}

        for vote in result:
            vote_data = {
                "username": vote[0],
                "created": vote[2].isoformat() if vote[2] else None,
            }

            if vote[1] == "upvote":
                voters["upvoters"].append(vote_data)
            else:
                voters["downvoters"].append(vote_data)

    return jsonify(voters)


@feature_requests_blueprint.route("/feature_requests/<int:request_id>/details")
def get_feature_request_details(request_id):
    """Get feature request details for editing"""
    with pg_session() as pg:
        result = pg.execute(
            fr_sql.get_feature_request_details(), {"request_id": request_id}
        ).fetchone()

        if result:
            return jsonify(
                {
                    "id": result[0],
                    "title": result[1],
                    "description": result[2],
                    "author": result[3],
                }
            )
        else:
            return jsonify({"error": "Feature request not found"}), 404


def _close_feature_request_and_notify(request_id, new_status, closure_reason=None):
    """Close a feature request and notify the author."""
    with pg_session() as pg:
        # Find author
        author_row = pg.execute(
            fr_sql.get_feature_request_author(), {"request_id": request_id}
        ).fetchone()
        author_username = author_row[0] if author_row else None

        # Update status + reason
        pg.execute(
            fr_sql.update_feature_request_status_with_reason(),
            {
                "request_id": request_id,
                "status": new_status,
                "closure_reason": closure_reason,
            },
        )

    # Send email if closed
    if new_status in ("completed", "not_doing", "merged") and author_username:
        try:
            subject = f"Your feature request #{request_id} was closed"
            if new_status == "completed":
                msg_status = "completed"
            elif new_status == "merged":
                msg_status = "merged"
            else:
                msg_status = "won't be done"

            user = User.query.filter_by(username=author_username).first()
            user_lang = user.lang
            message = f"{lang[user_lang]['fr_email_greeting'].format(username=author_username)}<br><br>"
            message += f"{lang[user_lang]['fr_email_feature_closed'].format(request_id=request_id, status=msg_status, url=url_for('feature_requests.single_feature_request', request_id=request_id, _external=True))}<br><br>"
            if closure_reason:
                message += f"{lang[user_lang]['fr_email_reason_label']}<br>{closure_reason}<br><br>"
            message += f"<i>{lang[user_lang]['fr_email_english_note']}</i><br><br>"
            message += f"{lang[user_lang]['fr_email_signature']}"
            sendEmailToUser(get_user_id(author_username), subject, message)
        except Exception as e:
            logger.exception("sendEmailToUser failed: %s", e)


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/update_status", methods=["POST"]
)
@owner_required
def update_feature_request_status(username):
    """Update status (owner only). If closing, store reason and notify author."""
    request_id = request.form["request_id"]
    new_status = request.form["status"]
    closure_reason = request.form.get("closure_reason", "").strip()

    # Clamp reason to closures only
    if new_status not in ("completed", "not_doing"):
        closure_reason = None

    _close_feature_request_and_notify(request_id, new_status, closure_reason)

    # Preserve redirect behavior
    referer = request.headers.get("Referer", "")
    if f"/feature_requests/{request_id}" in referer:
        return redirect(
            url_for("feature_requests.single_feature_request", request_id=request_id)
        )
    return redirect(url_for("feature_requests.feature_requests"))


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/merge", methods=["POST"]
)
@owner_required
def merge_feature_requests(username):
    """
    Merge two or more feature requests into a single target.
    - Body: target_id, source_ids (comma-separated)
    - Keeps only one vote per user (latest vote wins).
    - Source requests are CLOSED (not deleted) via fr_sql.close_feature_requests_bulk().
    """
    target_id = request.form.get("target_id")
    source_ids_raw = request.form.get("source_ids", "")  # e.g. "12,13,15"
    if not target_id or not source_ids_raw:
        return redirect(url_for("feature_requests.feature_requests"))
    try:
        target_id = int(target_id)
        source_ids = [int(x) for x in source_ids_raw.split(",") if x.strip()]
        source_ids = [sid for sid in source_ids if sid != target_id]
        if not source_ids:
            return redirect(
                url_for("feature_requests.single_feature_request", request_id=target_id)
            )
    except ValueError:
        return redirect(url_for("feature_requests.feature_requests"))

    with pg_session() as pg:
        # 1) Merge votes into target
        pg.execute(
            fr_sql.merge_votes_into_target(),
            {"target_id": target_id, "source_ids": source_ids},
        )
        # 2) Recompute vote counts on target
        pg.execute(fr_sql.update_vote_counts(), {"request_id": target_id})

    # 4) Notify authors of merged requests
    merge_reason = f"Merged into feature request #{target_id}"
    for source_id in source_ids:
        _close_feature_request_and_notify(source_id, "merged", merge_reason)

    return redirect(
        url_for("feature_requests.single_feature_request", request_id=target_id)
    )


@feature_requests_blueprint.route("/feature_requests/<int:request_id>/comments")
def get_comments(request_id):
    """Get all comments for a feature request as JSON"""
    with pg_session() as pg:
        result = pg.execute(
            fr_sql.list_comments(), {"request_id": request_id}
        ).fetchall()

        comments = []
        for row in result:
            comments.append(
                {
                    "id": row[0],
                    "feature_request_id": row[1],
                    "parent_id": row[2],
                    "username": "admin" if row[3] == owner else row[3],
                    "raw_username": row[3],
                    "content": row[4],
                    "created": row[5].isoformat() if row[5] else None,
                    "modified": row[6].isoformat() if row[6] else None,
                }
            )

    return jsonify(comments)


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/<int:request_id>/comment", methods=["POST"]
)
@login_required
def add_comment(username, request_id):
    """Add a new comment to a feature request"""
    content = request.form.get("content", "").strip()
    parent_id = request.form.get("parent_id") or None
    current_user = session["userinfo"]["logged_in_user"]
    display_name = "admin" if current_user == owner else current_user

    if not content:
        return jsonify({"error": "Content required"}), 400

    if parent_id:
        parent_id = int(parent_id)

    with pg_session() as pg:
        result = pg.execute(
            fr_sql.insert_comment(),
            {
                "request_id": request_id,
                "parent_id": parent_id,
                "username": current_user,
                "content": content,
            },
        ).fetchone()

        comment_id = result[0] if result else None

        # Get FR author for notification
        fr_author_row = pg.execute(
            fr_sql.get_feature_request_author(), {"request_id": request_id}
        ).fetchone()
        fr_author = fr_author_row[0] if fr_author_row else None

        notified_users = set()

        # Notify FR author on new top-level comment (not reply)
        if not parent_id and fr_author and fr_author != current_user:
            try:
                user = User.query.filter_by(username=fr_author).first()
                if user:
                    notified_users.add(fr_author)
                    user_lang = user.lang
                    subject = lang[user_lang].get(
                        "feature_requests_comment_new_subject",
                        "New comment on your feature request",
                    )
                    message = f"{lang[user_lang].get('fr_email_greeting', 'Hi {username}').format(username=fr_author)}<br><br>"
                    message += f"{display_name} {lang[user_lang].get('feature_requests_comment_new_body', 'commented on your feature request')}:<br><br>"
                    message += f'<i>"{content[:200]}{"..." if len(content) > 200 else ""}"</i><br><br>'
                    message += f'<a href="{url_for("feature_requests.single_feature_request", request_id=request_id, _external=True)}">{lang[user_lang].get("feature_requests_comment_view_link", "View the conversation")}</a>'
                    sendEmailToUser(get_user_id(fr_author), subject, message)
            except Exception as e:
                logger.exception("Failed to send FR comment notification: %s", e)

        # Notify parent comment author on reply
        if parent_id and comment_id:
            parent_info = pg.execute(
                fr_sql.get_comment_author(), {"comment_id": parent_id}
            ).fetchone()

            if (
                parent_info
                and parent_info[0] != current_user
                and parent_info[0] not in notified_users
            ):
                try:
                    parent_author = parent_info[0]
                    user = User.query.filter_by(username=parent_author).first()
                    if user:
                        user_lang = user.lang
                        subject = lang[user_lang].get(
                            "feature_requests_comment_reply_subject",
                            "Someone replied to your comment",
                        )
                        message = f"{lang[user_lang].get('fr_email_greeting', 'Hi {username}').format(username=parent_author)}<br><br>"
                        message += f"{display_name} {lang[user_lang].get('feature_requests_comment_reply_body', 'replied to your comment')}:<br><br>"
                        message += f'<i>"{content[:200]}{"..." if len(content) > 200 else ""}"</i><br><br>'
                        message += f'<a href="{url_for("feature_requests.single_feature_request", request_id=request_id, _external=True)}">{lang[user_lang].get("feature_requests_comment_view_link", "View the conversation")}</a>'
                        sendEmailToUser(get_user_id(parent_author), subject, message)
                except Exception as e:
                    logger.exception("Failed to send comment notification: %s", e)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "comment_id": comment_id})

    return redirect(
        url_for("feature_requests.single_feature_request", request_id=request_id)
    )


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/comment/<int:comment_id>/edit", methods=["POST"]
)
@login_required
def edit_comment(username, comment_id):
    """Edit a comment (owner can edit any, users can edit their own)"""
    content = request.form.get("content", "").strip()
    current_user = session["userinfo"]["logged_in_user"]
    is_owner_user = session["userinfo"].get("is_owner", False)

    if not content:
        return jsonify({"error": "Content required"}), 400

    with pg_session() as pg:
        author_info = pg.execute(
            fr_sql.get_comment_author(), {"comment_id": comment_id}
        ).fetchone()

        if not author_info:
            return jsonify({"error": "Comment not found"}), 404

        if not is_owner_user and author_info[0] != current_user:
            return jsonify({"error": "Not authorized"}), 403

        request_id = author_info[1]

        pg.execute(
            fr_sql.update_comment(), {"comment_id": comment_id, "content": content}
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True})

    return redirect(
        url_for("feature_requests.single_feature_request", request_id=request_id)
    )


@feature_requests_blueprint.route(
    "/u/<username>/feature_requests/comment/<int:comment_id>/delete", methods=["POST"]
)
@login_required
def delete_comment(username, comment_id):
    """Delete a comment (owner can delete any, users can delete their own)"""
    current_user = session["userinfo"]["logged_in_user"]
    is_owner_user = session["userinfo"].get("is_owner", False)

    with pg_session() as pg:
        author_info = pg.execute(
            fr_sql.get_comment_author(), {"comment_id": comment_id}
        ).fetchone()

        if not author_info:
            return jsonify({"error": "Comment not found"}), 404

        if not is_owner_user and author_info[0] != current_user:
            return jsonify({"error": "Not authorized"}), 403

        request_id = author_info[1]

        pg.execute(fr_sql.delete_comment(), {"comment_id": comment_id})

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True})

    return redirect(
        url_for("feature_requests.single_feature_request", request_id=request_id)
    )
