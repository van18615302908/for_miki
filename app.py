from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable, Sequence

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, UnidentifiedImageError
from psycopg import connect
from psycopg.rows import dict_row
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "stories.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ADMIN_PASSWORD = "miki_the_best_vb_player"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMAGE_SIZE = 512 * 1024  # 512KB upper limit
EXTERNAL_DB_URL = os.environ.get(
    "RAILWAY_EXTERNAL_DATABASE_URL",
    "postgresql://postgres:UklApTBLjMEZJMvGFtQRciNgzIkacjSr@caboose.proxy.rlwy.net:44350/railway",
)
INTERNAL_DB_URL = os.environ.get(
    "RAILWAY_INTERNAL_DATABASE_URL",
    "postgresql://postgres:UklApTBLjMEZJMvGFtQRciNgzIkacjSr@postgres.railway.internal:5432/railway",
)
CUSTOM_DB_URL = os.environ.get("DATABASE_URL")
IS_RAILWAY = any(
    os.environ.get(var)
    for var in (
        "RAILWAY_STATIC_URL",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_DEPLOYMENT_ID",
    )
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "kindness-exchange"
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
_db_initialized = False


# def get_connection():
#     candidates: list[str] = []
#     if CUSTOM_DB_URL:
#         candidates.append(CUSTOM_DB_URL)
#     else:
#         if IS_RAILWAY:
#             candidates.append(INTERNAL_DB_URL)
#         candidates.append(EXTERNAL_DB_URL)

#     last_exc: Exception | None = None
#     for url in candidates:
#         if not url:
#             continue
#         try:
#             return connect(url, row_factory=dict_row)
#         except Exception as exc:  # pragma: no cover - networking fallback
#             last_exc = exc
#             continue
#     raise RuntimeError("无法连接数据库，请检查 DATABASE_URL 设置。") from last_exc

def get_connection():
    candidates: list[str] = []
    if CUSTOM_DB_URL:
        candidates.append(CUSTOM_DB_URL)
    else:
        if IS_RAILWAY:
            candidates.append(INTERNAL_DB_URL)
        candidates.append(EXTERNAL_DB_URL)

    last_exc: Exception | None = None
    for url in candidates:
        if not url:
            continue
        try:
            # 🔥 打印使用的 URL（脱敏处理）
            safe_url = re.sub(r":\/\/([^:]+):([^@]+)@", r"://\\1:********@", url)
            print(f"[DB] Trying database: {safe_url}")

            conn = connect(url, row_factory=dict_row)

            print(f"[DB] Connected successfully! Using: {safe_url}")  # 再打印一次确认
            return conn
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError("无法连接数据库，请检查 DATABASE_URL 设置。") from last_exc



def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                likes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                image_path TEXT,
                is_approved INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS story_tags (
                story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (story_id, tag_id)
            )
            """
        )


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


def ensure_tag_ids(conn, selected_ids: Sequence[int | str], new_tag_names: Sequence[str]) -> list[int]:
    tag_ids: list[int] = []
    for tag_id in selected_ids:
        try:
            tag_ids.append(int(tag_id))
        except (TypeError, ValueError):
            continue

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    for name in new_tag_names:
        existing = conn.execute("SELECT id FROM tags WHERE name = %s", (name,)).fetchone()
        if existing:
            tag_ids.append(existing["id"])
            continue
        cur = conn.execute(
            "INSERT INTO tags (name, created_at) VALUES (%s, %s) RETURNING id",
            (name, timestamp),
        )
        tag_ids.append(cur.fetchone()["id"])

    unique_ids: list[int] = []
    seen: set[int] = set()
    for tag_id in tag_ids:
        if tag_id not in seen:
            seen.add(tag_id)
            unique_ids.append(tag_id)
    return unique_ids


def replace_story_tags(conn, story_id: int, tag_ids: Iterable[int]) -> None:
    conn.execute("DELETE FROM story_tags WHERE story_id = %s", (story_id,))
    pairs = [(story_id, tag_id) for tag_id in tag_ids]
    if pairs:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO story_tags (story_id, tag_id) VALUES (%s, %s)",
                pairs,
            )


def fetch_all_tags() -> list[dict]:
    with get_connection() as conn:
        return conn.execute("SELECT id, name FROM tags ORDER BY LOWER(name)").fetchall()


def fetch_tags_with_counts() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.id, t.name, COUNT(DISTINCT st.story_id) AS usage_count
            FROM tags t
            LEFT JOIN story_tags st ON st.tag_id = t.id
            LEFT JOIN stories s ON s.id = st.story_id AND s.is_approved = 1
            GROUP BY t.id
            HAVING COUNT(st.story_id) > 0
            ORDER BY LOWER(t.name)
            """
        ).fetchall()


def fetch_tags_admin_overview() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT t.id, t.name, COUNT(DISTINCT st.story_id) AS story_count
            FROM tags t
            LEFT JOIN story_tags st ON st.tag_id = t.id
            GROUP BY t.id
            ORDER BY LOWER(t.name)
            """
        ).fetchall()


def build_story_payload(row: dict) -> dict:
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
               COALESCE(string_agg(DISTINCT t.name, '||'), '') AS tag_names,
               COALESCE(string_agg(DISTINCT t.id::text, '||'), '') AS tag_ids
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


def compress_image_to_limit(file_storage) -> bytes:
    file_storage.stream.seek(0)
    try:
        image = Image.open(file_storage.stream)
    except UnidentifiedImageError as exc:
        raise ValueError("无法识别上传的图片，请改用 JPG/PNG 等常见格式。") from exc

    if getattr(image, "is_animated", False):
        image.seek(0)
    image = image.convert("RGB")

    quality = 90
    buffer = BytesIO()
    width, height = image.size

    while True:
        buffer.seek(0)
        buffer.truncate()
        image.save(buffer, format="JPEG", optimize=True, quality=quality)
        current_size = buffer.tell()

        if current_size <= MAX_IMAGE_SIZE or (quality <= 40 and max(width, height) <= 640):
            break

        if quality > 40:
            quality -= 10
        else:
            width = max(1, int(width * 0.9))
            height = max(1, int(height * 0.9))
            image = image.resize((width, height), Image.LANCZOS)

        if quality <= 20 and max(width, height) <= 480:
            break

    if buffer.tell() > MAX_IMAGE_SIZE:
        raise ValueError("图片尺寸过大，压缩后仍超过 512KB，请上传更小的照片。")

    return buffer.getvalue()


def save_uploaded_image(file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename or not allowed_image(filename):
        raise ValueError("仅支持 png/jpg/jpeg/gif/webp 格式的图片。")

    compressed_bytes = compress_image_to_limit(file_storage)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    final_name = f"{timestamp}.jpg"
    destination = UPLOAD_FOLDER / final_name
    with open(destination, "wb") as f:
        f.write(compressed_bytes)
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
                WHERE st2.story_id = s.id AND st2.tag_id = %s
            )
            """
        )
        params.append(tag_filter)

    if search_query:
        pattern = f"%{search_query}%"
        where_clauses.append("(s.title ILIKE %s OR s.body ILIKE %s OR s.name ILIKE %s)")
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
        photo_files = [f for f in request.files.getlist("photo") if f and f.filename]
        if len(photo_files) > 1:
            error = "一次最多上传 1 张照片。"
            story = {"name": name, "title": title, "body": body}
            return render_story_form(
                form_action=url_for("submit_story"),
                story=story,
                error=error,
                selected_tags=[int(t) for t in selected_tag_ids if t.isdigit()],
                new_tag_input=new_tag_input,
            )
        image_file = photo_files[0] if photo_files else None

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
        if image_file:
            try:
                image_path = save_uploaded_image(image_file)
            except ValueError as exc:
                story = {"name": name, "title": title, "body": body}
                return render_story_form(
                    form_action=url_for("submit_story"),
                    story=story,
                    error=str(exc),
                    selected_tags=[int(t) for t in selected_tag_ids if t.isdigit()],
                    new_tag_input=new_tag_input,
                )

        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO stories (name, title, body, likes, created_at, updated_at, image_path, is_approved)
                VALUES (%s, %s, %s, 0, %s, %s, %s, 0)
                RETURNING id
                """,
                (name, title, body, timestamp, timestamp, image_path),
            )
            story_id = cursor.fetchone()["id"]
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
                        WHERE st.tag_id = %s
                        """,
                        (tag_id,),
                    ).fetchall()
                    for row in story_rows:
                        conn.execute("DELETE FROM stories WHERE id = %s", (row["id"],))
                    conn.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
                    conn.commit()
                    info_message = "标签及其关联故事已删除。"
            else:
                story_id = request.form.get("story_id", type=int)
                if story_id is None:
                    error = "缺少故事 ID。"
                else:
                    if action == "delete_story":
                        conn.execute("DELETE FROM stories WHERE id = %s", (story_id,))
                        info_message = "故事已被删除。"
                    else:
                        conn.execute(
                            "UPDATE stories SET is_approved = 1, updated_at = %s WHERE id = %s",
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
            "SELECT id, likes FROM stories WHERE id = %s AND is_approved = 1",
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
                WHERE id = %s
                """,
                (story_id,),
            )
            liked_stories.remove(story_id)
        else:
            conn.execute(
                "UPDATE stories SET likes = likes + 1 WHERE id = %s",
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
