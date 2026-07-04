from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from .settings import get_db_path


def connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                parent_event_id TEXT,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                all_day INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '其他',
                location TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                reminder_minutes INTEGER,
                recurrence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(parent_event_id) REFERENCES events(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS event_exceptions (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                occurrence_start TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('cancel', 'modify')),
                overrides TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(event_id, occurrence_start),
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_start_at ON events(start_at);
            CREATE INDEX IF NOT EXISTS idx_event_exceptions_event_id
                ON event_exceptions(event_id);

            CREATE TABLE IF NOT EXISTS integration_syncs (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                external_key TEXT NOT NULL,
                event_id TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT NOT NULL DEFAULT '',
                last_payload TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, external_key),
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_integration_syncs_provider_updated
                ON integration_syncs(provider, updated_at);

            CREATE TABLE IF NOT EXISTS qq_messages (
                id TEXT PRIMARY KEY,
                external_key TEXT NOT NULL UNIQUE,
                group_id TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL DEFAULT '',
                sender_name TEXT NOT NULL DEFAULT '',
                course_name TEXT NOT NULL DEFAULT '',
                message_time TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                raw_payload TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'received',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_qq_messages_updated
                ON qq_messages(updated_at);

            CREATE TABLE IF NOT EXISTS qq_candidates (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                external_key TEXT NOT NULL UNIQUE,
                event_id TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                start_at TEXT,
                end_at TEXT,
                all_day INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '其他',
                location TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                reminder_minutes INTEGER,
                confidence REAL NOT NULL DEFAULT 0,
                missing_fields TEXT NOT NULL DEFAULT '[]',
                parse_source TEXT NOT NULL DEFAULT 'rules',
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT NOT NULL DEFAULT '',
                raw_result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(message_id) REFERENCES qq_messages(id) ON DELETE CASCADE,
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_qq_candidates_status_updated
                ON qq_candidates(status, updated_at);

            CREATE TABLE IF NOT EXISTS inbox_items (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                external_key TEXT NOT NULL,
                source_item_id TEXT NOT NULL DEFAULT '',
                source_api_base TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '其他',
                start_at TEXT,
                end_at TEXT,
                all_day INTEGER NOT NULL DEFAULT 0,
                location TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                action_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                event_id TEXT,
                raw_payload TEXT NOT NULL DEFAULT '{}',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, external_key),
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_inbox_items_status_updated
                ON inbox_items(status, updated_at);
            """
        )


def fetch_all_events() -> list[sqlite3.Row]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY start_at ASC").fetchall()
    return list(rows)


def fetch_event(event_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def fetch_exceptions(event_id: str) -> list[sqlite3.Row]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM event_exceptions WHERE event_id = ? ORDER BY occurrence_start",
            (event_id,),
        ).fetchall()
    return list(rows)


def fetch_exceptions_for_events(event_ids: Iterable[str]) -> dict[str, list[sqlite3.Row]]:
    ids = list(event_ids)
    if not ids:
        return {}

    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM event_exceptions
            WHERE event_id IN ({placeholders})
            ORDER BY event_id, occurrence_start
            """,
            ids,
        ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {event_id: [] for event_id in ids}
    for row in rows:
        grouped.setdefault(row["event_id"], []).append(row)
    return grouped


def insert_event(data: dict[str, Any]) -> dict[str, Any]:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    with connect() as conn:
        conn.execute(
            f"INSERT INTO events ({','.join(columns)}) VALUES ({placeholders})",
            [data[column] for column in columns],
        )
    return data


def update_event(event_id: str, data: dict[str, Any]) -> None:
    if not data:
        return
    assignments = ",".join(f"{column} = ?" for column in data)
    values = [data[column] for column in data]
    values.append(event_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE events SET {assignments} WHERE id = ?",
            values,
        )


def delete_event(event_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def upsert_exception(data: dict[str, Any]) -> None:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "event_id", "occurrence_start"}
    )
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO event_exceptions ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(event_id, occurrence_start)
            DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )


def delete_future_exceptions(event_id: str, occurrence_start: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM event_exceptions
            WHERE event_id = ? AND occurrence_start >= ?
            """,
            (event_id, occurrence_start),
        )


def fetch_integration_sync(provider: str, external_key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT * FROM integration_syncs
            WHERE provider = ? AND external_key = ?
            """,
            (provider, external_key),
        ).fetchone()


def list_integration_syncs(provider: str, limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM integration_syncs
            WHERE provider = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (provider, limit),
        ).fetchall()
    return list(rows)


def upsert_integration_sync(data: dict[str, Any]) -> None:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "provider", "external_key", "created_at"}
    )
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO integration_syncs ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(provider, external_key)
            DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )


def fetch_qq_message(external_key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM qq_messages WHERE external_key = ?",
            (external_key,),
        ).fetchone()


def upsert_qq_message(data: dict[str, Any]) -> sqlite3.Row:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "external_key", "created_at"}
    )
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO qq_messages ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(external_key)
            DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )
        return conn.execute(
            "SELECT * FROM qq_messages WHERE external_key = ?",
            (data["external_key"],),
        ).fetchone()


def fetch_qq_candidate(candidate_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM qq_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()


def fetch_qq_candidate_by_external_key(external_key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM qq_candidates WHERE external_key = ?",
            (external_key,),
        ).fetchone()


def list_qq_candidates(status_value: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        if status_value:
            rows = conn.execute(
                """
                SELECT * FROM qq_candidates
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status_value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM qq_candidates
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return list(rows)


def qq_candidate_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM qq_candidates
            GROUP BY status
            """
        ).fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def upsert_qq_candidate(data: dict[str, Any]) -> sqlite3.Row:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "external_key", "created_at"}
    )
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO qq_candidates ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(external_key)
            DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )
        return conn.execute(
            "SELECT * FROM qq_candidates WHERE external_key = ?",
            (data["external_key"],),
        ).fetchone()


def update_qq_candidate(candidate_id: str, data: dict[str, Any]) -> None:
    if not data:
        return
    assignments = ",".join(f"{column} = ?" for column in data)
    values = [data[column] for column in data]
    values.append(candidate_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE qq_candidates SET {assignments} WHERE id = ?",
            values,
        )


def fetch_inbox_item(item_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM inbox_items WHERE id = ?",
            (item_id,),
        ).fetchone()


def fetch_inbox_item_by_key(provider: str, external_key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM inbox_items WHERE provider = ? AND external_key = ?",
            (provider, external_key),
        ).fetchone()


def list_inbox_items(status_value: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        if status_value:
            rows = conn.execute(
                """
                SELECT * FROM inbox_items
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status_value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM inbox_items
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return list(rows)


def inbox_item_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM inbox_items GROUP BY status"
        ).fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def upsert_inbox_item(data: dict[str, Any]) -> sqlite3.Row:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"id", "provider", "external_key", "created_at"}
    )
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO inbox_items ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(provider, external_key)
            DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )
        return conn.execute(
            "SELECT * FROM inbox_items WHERE provider = ? AND external_key = ?",
            (data["provider"], data["external_key"]),
        ).fetchone()


def update_inbox_item(item_id: str, data: dict[str, Any]) -> None:
    if not data:
        return
    assignments = ",".join(f"{column} = ?" for column in data)
    values = [data[column] for column in data]
    values.append(item_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE inbox_items SET {assignments} WHERE id = ?",
            values,
        )
