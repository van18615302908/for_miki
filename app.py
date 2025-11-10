from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "stories.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "kindness-exchange"
_db_initialized = False


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                likes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def ensure_database() -> None:
    global _db_initialized
    if _db_initialized:
        return
    init_db()
    _db_initialized = True


@app.before_request
def before_request() -> None:
    ensure_database()


@app.route("/")
def index():
    with get_connection() as conn:
        stories = conn.execute(
            """
            SELECT id, name, title, body, likes, created_at, updated_at
            FROM stories
            ORDER BY likes DESC, updated_at DESC
            """
        ).fetchall()
    return render_template("index.html", stories=stories)


@app.route("/submit", methods=["GET", "POST"])
def submit_story():
    if request.method == "POST":
        name = request.form.get("name", "").strip() or "匿名同学"
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()

        if not title or not body:
            error = "标题和故事内容都需要填写～"
            return render_template(
                "form.html", error=error, form_action=url_for("submit_story"), story=None
            )

        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stories (name, title, body, likes, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (name, title, body, timestamp, timestamp),
            )
            conn.commit()
        return redirect(url_for("index"))

    return render_template("form.html", form_action=url_for("submit_story"), story=None)


def fetch_story_or_404(story_id: int) -> sqlite3.Row:
    with get_connection() as conn:
        story = conn.execute(
            """
            SELECT id, name, title, body
            FROM stories
            WHERE id = ?
            """,
            (story_id,),
        ).fetchone()
    if story is None:
        abort(404)
    return story


@app.route("/story/<int:story_id>/edit", methods=["GET", "POST"])
def edit_story(story_id: int):
    story = fetch_story_or_404(story_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip() or "匿名同学"
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()

        if not title or not body:
            error = "标题和故事内容都需要填写～"
            return render_template(
                "form.html",
                error=error,
                form_action=url_for("edit_story", story_id=story_id),
                story={"id": story_id, "name": name, "title": title, "body": body},
            )

        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE stories
                SET name = ?, title = ?, body = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, title, body, timestamp, story_id),
            )
            conn.commit()
        return redirect(url_for("index"))

    return render_template(
        "form.html",
        form_action=url_for("edit_story", story_id=story_id),
        story=story,
    )


@app.post("/story/<int:story_id>/like")
def like_story(story_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE stories SET likes = likes + 1 WHERE id = ?", (story_id,))
        conn.commit()
    referrer = request.referrer or url_for("index")
    return redirect(referrer)


if __name__ == "__main__":
    ensure_database()
    app.run(debug=True)
