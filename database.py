"""SQLite-backed storage for share links.

Set DATABASE_PATH to a persistent Render Disk path (for example
``/var/data/links.sqlite3``) if links must survive service restarts/deploys.
"""

import json
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = str(BASE_DIR / "links.sqlite3")
LEGACY_DATABASE_PATH = os.environ.get(
    "LEGACY_DATABASE_PATH", str(BASE_DIR / "links_db.json")
)
_initialized_paths = set()
_initialization_lock = threading.Lock()


def _database_path():
    path = os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip()
    if not path:
        path = DEFAULT_DATABASE_PATH
    if path == ":memory:":
        raise ValueError("DATABASE_PATH must be a file path, not :memory:")
    return os.path.abspath(os.path.expanduser(path))


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _import_legacy_json(connection):
    """Import the old JSON database once, if a deployment still has it."""
    migration = connection.execute(
        "SELECT value FROM app_metadata WHERE key = 'legacy_json_imported'"
    ).fetchone()
    if migration and migration[0] == "1":
        return

    legacy_path = Path(LEGACY_DATABASE_PATH)
    if legacy_path.is_file():
        try:
            with legacy_path.open("r", encoding="utf-8") as legacy_file:
                old_links = json.load(legacy_file)
            if not isinstance(old_links, dict):
                raise ValueError("legacy links database must contain a JSON object")

            for link_id, data in old_links.items():
                if not isinstance(data, dict) or not data.get("chat_id"):
                    continue
                # The old app could store browser/device details in visitor
                # records. Preserve aggregate photo counts, but never migrate
                # those personal metadata records.
                visitors = []
                connection.execute(
                    """
                    INSERT OR IGNORE INTO links (
                        link_id, chat_id, image_url, custom_message, created_at,
                        visitors_json, photos_received, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(link_id),
                        str(data["chat_id"]),
                        str(data.get("image_url", "")),
                        str(data.get("custom_message", "")),
                        str(data.get("created_at") or _utc_now()),
                        json.dumps(visitors),
                        max(0, int(data.get("photos_received", 0) or 0)),
                        int(bool(data.get("is_active", True))),
                    ),
                )
            logger.info("Imported legacy links from %s", legacy_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.exception("Could not import legacy links from %s", legacy_path)

    connection.execute(
        "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
        ("legacy_json_imported", "1"),
    )


def _connect():
    path = _database_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 15000")

    # Schema creation is safe across app threads and multiple processes.
    with _initialization_lock:
        if path not in _initialized_paths:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS links (
                    link_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    custom_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    visitors_json TEXT NOT NULL DEFAULT '[]',
                    photos_received INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            _import_legacy_json(connection)
            connection.commit()
            _initialized_paths.add(path)

    return connection


@contextmanager
def _database():
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode_link(row):
    if row is None:
        return None
    try:
        visitors = json.loads(row["visitors_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        visitors = []
    return {
        "chat_id": row["chat_id"],
        "image_url": row["image_url"],
        "custom_message": row["custom_message"],
        "created_at": row["created_at"],
        "visitors": visitors,
        "photos_received": int(row["photos_received"]),
        "is_active": bool(row["is_active"]),
    }


def create_link(chat_id, image_url, custom_message=""):
    """Create an unguessable link and return its ID."""
    with _database() as connection:
        for _ in range(5):
            # 144 bits of URL-safe randomness; substantially safer than the old
            # eight-character IDs while still easy to embed in a URL.
            link_id = secrets.token_urlsafe(18)
            try:
                connection.execute(
                    """
                    INSERT INTO links (
                        link_id, chat_id, image_url, custom_message, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        link_id,
                        str(chat_id),
                        str(image_url),
                        str(custom_message or ""),
                        _utc_now(),
                    ),
                )
                return link_id
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("Could not generate a unique link ID")


def get_link(link_id):
    """Return link data, or None if the ID is unknown."""
    with _database() as connection:
        row = connection.execute(
            "SELECT * FROM links WHERE link_id = ?", (str(link_id),)
        ).fetchone()
    return _decode_link(row)


def add_visitor(link_id, visitor_info="Photo shared with consent"):
    """Increment the photo count after a visitor explicitly sends a photo.

    ``visitor_info`` remains accepted for compatibility but is deliberately not
    saved. No visitor IP, timestamp, browser fingerprint, or device details are
    collected for new links.
    """
    del visitor_info
    with _database() as connection:
        cursor = connection.execute(
            """
            UPDATE links
            SET photos_received = photos_received + 1
            WHERE link_id = ? AND is_active = 1
            """,
            (str(link_id),),
        )
        return cursor.rowcount > 0


def get_user_links(chat_id):
    """Return a mapping of this Telegram user's links."""
    with _database() as connection:
        rows = connection.execute(
            "SELECT * FROM links WHERE chat_id = ? ORDER BY created_at DESC",
            (str(chat_id),),
        ).fetchall()
    return {row["link_id"]: _decode_link(row) for row in rows}


def deactivate_link(link_id):
    """Deactivate a link. Callers should verify its owner first."""
    with _database() as connection:
        cursor = connection.execute(
            "UPDATE links SET is_active = 0 WHERE link_id = ?", (str(link_id),)
        )
        return cursor.rowcount > 0
