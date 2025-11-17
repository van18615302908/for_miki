from __future__ import annotations

import sqlite3
from pathlib import Path

from psycopg import connect

from app import DATABASE_PATH, DATABASE_URL, init_db


def migrate_sqlite_to_postgres(sqlite_path: Path) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"找不到 SQLite 数据库：{sqlite_path}")

    init_db()

    with sqlite3.connect(sqlite_path) as sqlite_conn, connect(DATABASE_URL) as pg_conn:
        sqlite_conn.row_factory = sqlite3.Row

        with pg_conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE story_tags RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE TABLE tags RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE TABLE stories RESTART IDENTITY CASCADE")

        stories = sqlite_conn.execute(
            "SELECT id, name, title, body, likes, created_at, updated_at, image_path, is_approved FROM stories"
        ).fetchall()
        tags = sqlite_conn.execute("SELECT id, name, created_at FROM tags").fetchall()
        story_tags = sqlite_conn.execute("SELECT story_id, tag_id FROM story_tags").fetchall()

        with pg_conn.cursor() as cur:
            for story in stories:
                cur.execute(
                    """
                    INSERT INTO stories (id, name, title, body, likes, created_at, updated_at, image_path, is_approved)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        story["id"],
                        story["name"],
                        story["title"],
                        story["body"],
                        story["likes"],
                        story["created_at"],
                        story["updated_at"],
                        story["image_path"],
                        story["is_approved"],
                    ),
                )

            for tag in tags:
                cur.execute(
                    "INSERT INTO tags (id, name, created_at) VALUES (%s, %s, %s)",
                    (tag["id"], tag["name"], tag["created_at"]),
                )

            for link in story_tags:
                cur.execute(
                    "INSERT INTO story_tags (story_id, tag_id) VALUES (%s, %s)",
                    (link["story_id"], link["tag_id"]),
                )

            cur.execute(
                "SELECT setval('stories_id_seq', COALESCE((SELECT MAX(id) FROM stories), 1), true)"
            )
            cur.execute("SELECT setval('tags_id_seq', COALESCE((SELECT MAX(id) FROM tags), 1), true)")

        pg_conn.commit()

    print(
        f"迁移完成：{len(stories)} 条故事、{len(tags)} 个标签、{len(story_tags)} 条关系已写入 Railway Postgres。"
    )


if __name__ == "__main__":
    migrate_sqlite_to_postgres(DATABASE_PATH)
