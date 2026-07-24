import logging
from datetime import datetime

import markdown
from flask import (
    Blueprint,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.pg import pg_session
from src.sql import news as news_sql
from src.utils import get_user_id, has_current_trip, lang, owner, owner_required

logger = logging.getLogger(__name__)

news_blueprint = Blueprint("news", __name__)


@news_blueprint.route("/api/news/count")
def get_news_count():
    """Get count of news items since the user's last visit of the news page"""
    current_user = session.get("userinfo", {}).get("logged_in_user")
    if not current_user or current_user == "public":
        return jsonify({"count": 0})

    with pg_session() as pg:
        result = pg.execute(
            news_sql.count_unseen_news(), {"username": current_user}
        ).fetchone()
        count = result[0] if result else 0

    return jsonify({"count": count})


@news_blueprint.route("/api/news/count/app/<date_last_visit>")
def get_news_count_app(date_last_visit):
    """Get count of news items since last visit"""
    try:
        date_last_visit = datetime.fromisoformat(date_last_visit)
    except (ValueError, TypeError):
        # Should we put now to get 0, or a date in the past to get all the news count?
        date_last_visit = datetime.now()
        # date_last_visit = datetime(1970, 1, 1, 0, 0, 0)

    try:
        with pg_session() as pg:
            result = pg.execute(
                news_sql.count_news_since_date(), {"last_visit": date_last_visit}
            ).fetchone()

            count = result[0] if result else 0
            return jsonify({"count": count})
    except (ValueError, TypeError):
        return jsonify({"count": 0})


def news_row_to_dict(item):
    """Build a news dict from a list_news row, with markdown-rendered HTML"""
    return {
        "id": item[0],
        "title": item[1],
        "content": item[2],
        "content_html": markdown.markdown(
            item[2], extensions=["nl2br", "fenced_code", "tables"]
        ),
        "author_display": "admin" if item[3] == owner else item[3],
        "created": item[4],
        "last_modified": item[5],
    }


@news_blueprint.route("/api/news/app")
def news_app():
    """Get news list for app"""

    with pg_session() as pg:
        result = pg.execute(news_sql.list_news()).fetchall()
        news_list = [news_row_to_dict(item) for item in result]

    return jsonify(news_list), 200


@news_blueprint.route("/news")
def news(username=None):
    """Display news page"""
    userinfo = session.get("userinfo", {})
    current_user = userinfo.get("logged_in_user")

    with pg_session() as pg:
        result = pg.execute(news_sql.list_news()).fetchall()
        news_list = [news_row_to_dict(item) for item in result]

        if current_user and current_user != "public":
            pg.execute(
                news_sql.upsert_news_visit(), {"username": current_user}
            )

    response = make_response(
        render_template(
            "news.html",
            username=current_user,
            news_list=news_list,
            **lang.get(userinfo.get("lang", "en"), {}),
            **userinfo,
            nav="bootstrap/navigation.html"
            if current_user != "public"
            else "bootstrap/no_user_nav.html",
            isCurrent=has_current_trip(get_user_id())
            if current_user != "public"
            else False,
        )
    )

    return response


@news_blueprint.route("/u/<username>/news/submit", methods=["POST"])
@owner_required
def submit_news(username):
    """Submit a new news item (owner only)"""
    title = request.form["title"]
    content = request.form["content"]
    current_user = session["userinfo"]["logged_in_user"]

    with pg_session() as pg:
        pg.execute(
            news_sql.insert_news(),
            {"title": title, "content": content, "username": current_user},
        ).fetchone()

    return redirect(url_for("news.news"))


@news_blueprint.route("/u/<username>/news/edit", methods=["POST"])
@owner_required
def edit_news(username):
    """Edit a news item (owner only)"""
    news_id = request.form["news_id"]
    title = request.form["title"]
    content = request.form["content"]

    with pg_session() as pg:
        pg.execute(
            news_sql.update_news(),
            {"news_id": news_id, "title": title, "content": content},
        )

    return redirect(url_for("news.news"))


@news_blueprint.route("/u/<username>/news/delete", methods=["POST"])
@owner_required
def delete_news(username):
    """Delete a news item (owner only)"""
    news_id = request.form["news_id"]

    with pg_session() as pg:
        pg.execute(news_sql.delete_news(), {"news_id": news_id})

    return redirect(url_for("news.news"))


@news_blueprint.route("/news/<int:news_id>/details")
def get_news_details(news_id):
    """Get news details for editing"""
    with pg_session() as pg:
        result = pg.execute(news_sql.get_single_news(), {"news_id": news_id}).fetchone()

        if result:
            return jsonify(
                {
                    "id": result[0],
                    "title": result[1],
                    "content": result[2],
                    "author": result[3],
                }
            )
        else:
            return jsonify({"error": "News item not found"}), 404
