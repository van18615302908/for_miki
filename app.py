from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "stories.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ADMIN_PASSWORD = "miki_the_best_vb_player"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = "kindness-exchange"
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
_db_initialized = False


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_story_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stories)")}
    if "image_path" not in columns:
        conn.execute("ALTER TABLE stories ADD COLUMN image_path TEXT")
    if "is_approved" not in columns:
        conn.execute("ALTER TABLE stories ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 0")


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS story_tags (
                story_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (story_id, tag_id),
                FOREIGN KEY (story_id) REFERENCES stories (id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
            )
            """
        )
        ensure_story_columns(conn)
        conn.commit()


def ensure_database() -> None:
    global _db_initialized
    if _db_initialized:
        return
    init_db()
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    _db_initialized = True


@app.before_request
def before_request() -> None:
    ensure_database()


def parse_new_tag_names(raw: str) -> list[str]:
    if not raw:
        return []
    pieces = [chunk.strip() for chunk in re.split(r"[,\s]+", raw) if chunk.strip()]
    seen: list[str] = []
    for piece in pieces:
        if piece not in seen:
            seen.append(piece)
    return seen


def ensure_tag_ids(
    conn: sqlite3.Connection, selected_ids: Sequence[int | str], new_tag_names: Sequence[str]
) -> list[int]:
    tag_ids: list[int] = []
    for tag_id in selected_ids:
        try:
            tag_ids.append(int(tag_id))
        except (TypeError, ValueError):
            continue

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    for name in new_tag_names:
        existing = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if existing:
            tag_ids.append(existing["id"])
            continue
        cur = conn.execute(
            "INSERT INTO tags (name, created_at) VALUES (?, ?)",
            (name, timestamp),
        )
        tag_ids.append(cur.lastrowid)

    unique_ids: list[int] = []
    seen: set[int] = set()
    for tag_id in tag_ids:
        if tag_id not in seen:
            seen.add(tag_id)
            unique_ids.append(tag_id)
    return unique_ids


def replace_story_tags(conn: sqlite3.Connection, story_id: int, tag_ids: Iterable[int]) -> None:
    conn.execute("DELETE FROM story_tags WHERE story_id = ?", (story_id,))
    pairs = [(story_id, tag_id) for tag_id in tag_ids]
    if pairs:
        conn.executemany(
            "INSERT INTO story_tags (story_id, tag_id) VALUES (?, ?)",
            pairs,
        )


def fetch_all_tags() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT id, name FROM tags ORDER BY name COLLATE NOCASE").fetchall()


def fetch_tags_with_counts() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.id, t.name, COUNT(DISTINCT st.story_id) AS usage_count
            FROM tags t
            LEFT JOIN story_tags st ON st.tag_id = t.id
            LEFT JOIN stories s ON s.id = st.story_id AND s.is_approved = 1
            GROUP BY t.id
            HAVING usage_count > 0
            ORDER BY t.name COLLATE NOCASE
            """
        ).fetchall()


def fetch_tags_admin_overview() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.id, t.name, COUNT(DISTINCT st.story_id) AS story_count
            FROM tags t
            LEFT JOIN story_tags st ON st.tag_id = t.id
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """
        ).fetchall()


def build_story_payload(row: sqlite3.Row) -> dict:
    story = dict(row)
    tag_names = story.pop("tag_names", "") or ""
    tag_ids = story.pop("tag_ids", "") or ""
    names = [name for name in tag_names.split("||") if name]
    ids = [tid for tid in tag_ids.split("||") if tid]
    story["tags"] = []
    for tid, name in zip(ids, names):
        try:
            story["tags"].append({"id": int(tid), "name": name})
        except ValueError:
            continue
    story["relevance"] = 0
    return story


def load_stories(
    *,
    where_clauses: Sequence[str] | None = None,
    params: Sequence[object] | None = None,
    order_sql: str = "ORDER BY s.likes DESC, s.updated_at DESC",
) -> list[dict]:
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
    query = f"""
        SELECT s.id, s.name, s.title, s.body, s.likes,
               s.created_at, s.updated_at, s.image_path, s.is_approved,
               COALESCE(GROUP_CONCAT(t.name, '||'), '') AS tag_names,
               COALESCE(GROUP_CONCAT(t.id, '||'), '') AS tag_ids
        FROM stories s
        LEFT JOIN story_tags st ON st.story_id = s.id
        LEFT JOIN tags t ON t.id = st.tag_id
        {where_sql}
        GROUP BY s.id
        {order_sql}
    """
    with get_connection() as conn:
        rows = conn.execute(query, params or []).fetchall()
    return [build_story_payload(row) for row in rows]


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename or not allowed_image(filename):
        return None
    suffix = Path(filename).suffix.lower()
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    final_name = f"{timestamp}{suffix}"
    destination = UPLOAD_FOLDER / final_name
    file_storage.save(destination)
    return f"uploads/{final_name}"


def render_story_form(
    *,
    form_action: str,
    story: dict | None,
    error: str | None = None,
    selected_tags: Sequence[int] | None = None,
    new_tag_input: str = "",
) -> str:
    return render_template(
        "form.html",
        error=error,
        form_action=form_action,
        story=story,
        tags=fetch_all_tags(),
        selected_tags=list(selected_tags or []),
        new_tag_input=new_tag_input,
    )


@app.route("/")
def index():
    tag_filter = request.args.get("tag", type=int)
    search_query = request.args.get("q", "").strip()
    sort_param = request.args.get("sort", "likes")
    submitted_flag = request.args.get("submitted")

    where_clauses = ["s.is_approved = 1"]
    params: list[object] = []

    if tag_filter:
        where_clauses.append(
            """
            EXISTS (
                SELECT 1 FROM story_tags st2
                WHERE st2.story_id = s.id AND st2.tag_id = ?
            )
            """
        )
        params.append(tag_filter)

    if search_query:
        pattern = f"%{search_query}%"
        where_clauses.append("(s.title LIKE ? OR s.body LIKE ? OR s.name LIKE ?)")
        params.extend([pattern, pattern, pattern])

    if sort_param not in {"likes", "latest"}:
        sort_param = "likes"
    order_sql = "ORDER BY s.likes DESC, s.updated_at DESC"
    if sort_param == "latest":
        order_sql = "ORDER BY s.updated_at DESC"

    stories = load_stories(where_clauses=where_clauses, params=params, order_sql=order_sql)

    if search_query:
        needle = search_query.lower()
        for story in stories:
            title_score = story["title"].lower().count(needle) * 2
            body_score = story["body"].lower().count(needle)
            name_score = story["name"].lower().count(needle)
            story["relevance"] = title_score + body_score + name_score
        if sort_param == "likes":
            stories.sort(key=lambda s: (s["relevance"], s["likes"]), reverse=True)
        elif sort_param == "latest":
            stories.sort(key=lambda s: (s["relevance"], s["updated_at"]), reverse=True)

    info_message = "故事已提交，等待心理老师审核后发布。" if submitted_flag else None

    liked_story_ids = session.get("liked_stories", [])

    return render_template(
        "index.html",
        stories=stories,
        tags=fetch_tags_with_counts(),
        active_tag=tag_filter,
        search_query=search_query,
        sort_param=sort_param,
        info_message=info_message,
        liked_story_ids=liked_story_ids,
    )


def validate_name(name: str) -> bool:
    return bool(name and len(name) >= 4 and bool(re.search(r"\s", name)))


@app.route("/submit", methods=["GET", "POST"])
def submit_story():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        selected_tag_ids = request.form.getlist("tags")
        new_tag_input = request.form.get("new_tags", "").strip()
        new_tag_names = parse_new_tag_names(new_tag_input)
        image_file = request.files.get("photo")

        if not validate_name(name):
            error = "昵称需包含班级和姓名，例如“高一3班 张三”。"
            story = {"name": name, "title": title, "body": body}
            return render_story_form(
                form_action=url_for("submit_story"),
                story=story,
                error=error,
                selected_tags=[int(t) for t in selected_tag_ids if t.isdigit()],
                new_tag_input=new_tag_input,
            )

        if not title or not body:
            error = "标题和故事内容都需要填写～"
            story = {"name": name, "title": title, "body": body}
            return render_story_form(
                form_action=url_for("submit_story"),
                story=story,
                error=error,
                selected_tags=[int(t) for t in selected_tag_ids if t.isdigit()],
                new_tag_input=new_tag_input,
            )

        image_path = None
        if image_file and image_file.filename:
            if not allowed_image(image_file.filename):
                error = "仅支持 png/jpg/jpeg/gif/webp 图片格式。"
                story = {"name": name, "title": title, "body": body}
                return render_story_form(
                    form_action=url_for("submit_story"),
                    story=story,
                    error=error,
                    selected_tags=[int(t) for t in selected_tag_ids if t.isdigit()],
                    new_tag_input=new_tag_input,
                )
            image_path = save_uploaded_image(image_file)

        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO stories (name, title, body, likes, created_at, updated_at, image_path, is_approved)
                VALUES (?, ?, ?, 0, ?, ?, ?, 0)
                """,
                (name, title, body, timestamp, timestamp, image_path),
            )
            story_id = cursor.lastrowid
            tag_ids = ensure_tag_ids(conn, selected_tag_ids, new_tag_names)
            replace_story_tags(conn, story_id, tag_ids)
            conn.commit()
        return redirect(url_for("index", submitted=1))

    return render_story_form(form_action=url_for("submit_story"), story=None)


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if not session.get("is_admin"):
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == ADMIN_PASSWORD:
                session["is_admin"] = True
                return redirect(url_for("admin_panel"))
            error = "管理员密码错误。"
        return render_template("admin_login.html", error=error)

    error = None
    info_message = None
    if request.method == "POST":
        action = request.form.get("action", "approve")
        with get_connection() as conn:
            if action == "delete_tag":
                tag_id = request.form.get("tag_id", type=int)
                if tag_id is None:
                    error = "缺少标签 ID。"
                else:
                    story_rows = conn.execute(
                        """
                        SELECT DISTINCT s.id
                        FROM stories s
                        JOIN story_tags st ON st.story_id = s.id
                        WHERE st.tag_id = ?
                        """,
                        (tag_id,),
                    ).fetchall()
                    for row in story_rows:
                        conn.execute("DELETE FROM stories WHERE id = ?", (row["id"],))
                    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
                    conn.commit()
                    info_message = "标签及其关联故事已删除。"
            else:
                story_id = request.form.get("story_id", type=int)
                if story_id is None:
                    error = "缺少故事 ID。"
                else:
                    if action == "delete_story":
                        conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
                        info_message = "故事已被删除。"
                    else:
                        conn.execute(
                            "UPDATE stories SET is_approved = 1, updated_at = ? WHERE id = ?",
                            (datetime.utcnow().isoformat(timespec="seconds"), story_id),
                        )
                        info_message = "故事已审核发布。"
                    conn.commit()

    pending_stories = load_stories(
        where_clauses=["s.is_approved = 0"],
        order_sql="ORDER BY s.created_at ASC",
    )
    published_stories = load_stories(
        where_clauses=["s.is_approved = 1"],
        order_sql="ORDER BY s.updated_at DESC",
    )[:5]
    all_stories = load_stories(order_sql="ORDER BY s.updated_at DESC")
    tag_overview = fetch_tags_admin_overview()

    return render_template(
        "admin.html",
        error=error,
        info_message=info_message,
        pending_stories=pending_stories,
        published_stories=published_stories,
        all_stories=all_stories,
        tag_overview=tag_overview,
    )


@app.post("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.post("/story/<int:story_id>/like")
def like_story(story_id: int):
    liked_stories = {int(sid) for sid in session.get("liked_stories", [])}

    with get_connection() as conn:
        story = conn.execute(
            "SELECT id, likes FROM stories WHERE id = ? AND is_approved = 1",
            (story_id,),
        ).fetchone()
        if story is None:
            referrer = request.referrer or url_for("index")
            return redirect(referrer)

        if story_id in liked_stories:
            conn.execute(
                """
                UPDATE stories
                SET likes = CASE WHEN likes > 0 THEN likes - 1 ELSE 0 END
                WHERE id = ?
                """,
                (story_id,),
            )
            liked_stories.remove(story_id)
        else:
            conn.execute(
                "UPDATE stories SET likes = likes + 1 WHERE id = ?",
                (story_id,),
            )
            liked_stories.add(story_id)
        conn.commit()

    session["liked_stories"] = list(liked_stories)

    referrer = request.referrer or url_for("index")
    return redirect(referrer)


if __name__ == "__main__":
    ensure_database()
    app.run(debug=True)
