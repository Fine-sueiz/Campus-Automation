from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_db_path(root: Path) -> Path:
    return root / "data" / "campus_monitor.sqlite3"


class MonitorDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL UNIQUE,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    published TEXT,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY,
                    article_item_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    article_url TEXT NOT NULL,
                    signup_url TEXT,
                    activity_time TEXT,
                    deadline TEXT,
                    location TEXT,
                    schedule_status TEXT NOT NULL,
                    free_time_text TEXT,
                    matched_time_text TEXT,
                    raw_text TEXT,
                    status TEXT NOT NULL,
                    feishu_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    operator_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS form_tasks (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    signup_url TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_path TEXT,
                    exit_code INTEGER,
                    log TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bindings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_sends (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    article_item_id TEXT,
                    recipient TEXT NOT NULL,
                    subject TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, recipient)
                );

                CREATE TABLE IF NOT EXISTS volunteer_confirmations (
                    token TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    invite_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invite_codes (
                    code TEXT PRIMARY KEY,
                    label TEXT,
                    max_uses INTEGER NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    student_id TEXT,
                    email TEXT,
                    college TEXT,
                    grade TEXT,
                    wechat TEXT,
                    answers_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_schedules (
                    user_id TEXT PRIMARY KEY,
                    schedule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_opportunities (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    schedule_status TEXT NOT NULL,
                    free_time_text TEXT,
                    matched_time_text TEXT,
                    status TEXT NOT NULL,
                    pushed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, opportunity_id)
                );

                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL UNIQUE,
                    subscription_json TEXT NOT NULL,
                    user_agent TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feed_configs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS calendar_syncs (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    calendar_event_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS schedule_inbox_syncs (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    inbox_item_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS wechat_captures (
                    external_key TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_text TEXT,
                    article_url TEXT,
                    raw_text TEXT,
                    status TEXT NOT NULL,
                    opportunity_id TEXT,
                    error TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_fingerprints (
                    key TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    article_item_id TEXT NOT NULL,
                    source_name TEXT,
                    title TEXT,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feed_health (
                    url TEXT PRIMARY KEY,
                    name TEXT,
                    last_status TEXT NOT NULL,
                    last_error TEXT,
                    last_items INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    total_ok INTEGER NOT NULL DEFAULT 0,
                    total_failed INTEGER NOT NULL DEFAULT 0,
                    last_ok_at TEXT,
                    last_failed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(form_tasks)").fetchall()
            }
            if "user_id" not in existing_columns:
                conn.execute("ALTER TABLE form_tasks ADD COLUMN user_id TEXT")
            opportunity_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()
            }
            if "score" not in opportunity_columns:
                conn.execute("ALTER TABLE opportunities ADD COLUMN score INTEGER")
            if "score_reasons" not in opportunity_columns:
                conn.execute("ALTER TABLE opportunities ADD COLUMN score_reasons TEXT")

    def find_content_fingerprints(self, keys: list[str]) -> list[dict[str, Any]]:
        """按指纹键查全部命中记录（最早登记在前）；任一键命中即视为同一内容。"""
        clean_keys = [key for key in keys if key]
        if not clean_keys:
            return []
        placeholders = ",".join("?" for _ in clean_keys)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM content_fingerprints WHERE key IN ({placeholders}) "
                "ORDER BY first_seen_at ASC",
                clean_keys,
            ).fetchall()
        return [dict(row) for row in rows]

    def register_content_fingerprints(
        self,
        keys: list[str],
        opportunity_id: str,
        article_item_id: str,
        source_name: str = "",
        title: str = "",
        *,
        reset: bool = False,
    ) -> None:
        """登记/累计指纹。reset=True 表示旧指纹已过窗，整体替换为新一期机会。"""
        now = utc_now()
        with self.connect() as conn:
            for key in keys:
                if not key:
                    continue
                if reset:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO content_fingerprints(
                            key, opportunity_id, article_item_id, source_name, title,
                            seen_count, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (key, opportunity_id, article_item_id, source_name, title, now, now),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO content_fingerprints(
                            key, opportunity_id, article_item_id, source_name, title,
                            seen_count, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            seen_count = seen_count + 1,
                            last_seen_at = excluded.last_seen_at
                        """,
                        (key, opportunity_id, article_item_id, source_name, title, now, now),
                    )

    def article_exists(self, item_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM articles WHERE item_id = ?", (item_id,)).fetchone()
        return row is not None

    def insert_article(self, item_id: str, source_name: str, title: str, link: str, published: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO articles(item_id, source_name, title, link, published, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item_id, source_name, title, link, published, utc_now()),
            )

    def get_wechat_capture(self, external_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM wechat_captures WHERE external_key = ?",
                (external_key,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_wechat_capture(
        self,
        external_key: str,
        source_name: str,
        title: str,
        published_text: str,
        article_url: str,
        raw_text: str,
        status: str,
        *,
        opportunity_id: str = "",
        error: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT received_at FROM wechat_captures WHERE external_key = ?",
                (external_key,),
            ).fetchone()
            received_at = str(existing["received_at"]) if existing else now
            conn.execute(
                """
                INSERT INTO wechat_captures(
                    external_key, source_name, title, published_text, article_url,
                    raw_text, status, opportunity_id, error, received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_key) DO UPDATE SET
                    source_name = excluded.source_name,
                    title = excluded.title,
                    published_text = excluded.published_text,
                    article_url = excluded.article_url,
                    raw_text = excluded.raw_text,
                    status = excluded.status,
                    opportunity_id = excluded.opportunity_id,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    external_key,
                    source_name,
                    title,
                    published_text,
                    article_url,
                    raw_text,
                    status,
                    opportunity_id,
                    error,
                    received_at,
                    now,
                ),
            )

    def list_wechat_captures(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM wechat_captures ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_opportunity(self, payload: dict[str, Any]) -> None:
        now = utc_now()
        payload = dict(payload)
        payload.setdefault("created_at", now)
        payload.setdefault("score", None)
        payload.setdefault("score_reasons", "")
        payload["updated_at"] = now
        with self.connect() as conn:
            existing = conn.execute("SELECT created_at FROM opportunities WHERE id = ?", (payload["id"],)).fetchone()
            if existing:
                payload["created_at"] = existing["created_at"]
            conn.execute(
                """
                INSERT INTO opportunities (
                    id, article_item_id, category, title, source_name, article_url, signup_url,
                    activity_time, deadline, location, schedule_status, free_time_text,
                    matched_time_text, raw_text, status, feishu_message_id, score, score_reasons,
                    created_at, updated_at
                )
                VALUES (
                    :id, :article_item_id, :category, :title, :source_name, :article_url, :signup_url,
                    :activity_time, :deadline, :location, :schedule_status, :free_time_text,
                    :matched_time_text, :raw_text, :status, :feishu_message_id, :score, :score_reasons,
                    :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    signup_url = excluded.signup_url,
                    activity_time = excluded.activity_time,
                    deadline = excluded.deadline,
                    location = excluded.location,
                    schedule_status = excluded.schedule_status,
                    free_time_text = excluded.free_time_text,
                    matched_time_text = excluded.matched_time_text,
                    raw_text = excluded.raw_text,
                    status = excluded.status,
                    feishu_message_id = excluded.feishu_message_id,
                    score = excluded.score,
                    score_reasons = excluded.score_reasons,
                    updated_at = excluded.updated_at
                """,
                payload,
            )

    def get_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
        return dict(row) if row else None

    def list_opportunities(
        self,
        *,
        since: str | None = None,
        min_score: int | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        # since 必须与 utc_now() 同格式（UTC ISO、秒精度、+00:00 后缀），定长字符串可直接比较
        query = "SELECT * FROM opportunities"
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if min_score is not None:
            clauses.append("score IS NOT NULL AND score >= ?")
            params.append(min_score)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_opportunity_status(self, opportunity_id: str, status: str, message_id: str | None = None) -> None:
        with self.connect() as conn:
            if message_id is None:
                conn.execute(
                    "UPDATE opportunities SET status = ?, updated_at = ? WHERE id = ?",
                    (status, utc_now(), opportunity_id),
                )
            else:
                conn.execute(
                    "UPDATE opportunities SET status = ?, feishu_message_id = ?, updated_at = ? WHERE id = ?",
                    (status, message_id, utc_now(), opportunity_id),
                )

    def email_send_exists(self, opportunity_id: str, recipient: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM email_sends WHERE opportunity_id = ? AND recipient = ? AND status IN ('sent', 'dry_run_sent')",
                (opportunity_id, recipient),
            ).fetchone()
        return row is not None

    def record_email_send(
        self,
        send_id: str,
        opportunity_id: str,
        article_item_id: str,
        recipient: str,
        subject: str,
        status: str,
        error: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO email_sends(
                    id, opportunity_id, article_item_id, recipient, subject, status, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id, recipient) DO UPDATE SET
                    subject = excluded.subject,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (send_id, opportunity_id, article_item_id, recipient, subject, status, error, now, now),
            )

    def get_volunteer_confirmation_by_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM volunteer_confirmations WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_volunteer_confirmation(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM volunteer_confirmations WHERE token = ?",
                (token.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def create_volunteer_confirmation(
        self,
        token: str,
        opportunity_id: str,
        expires_at: str,
        status: str = "pending",
    ) -> dict[str, Any]:
        token = token.upper()
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO volunteer_confirmations(
                    token, opportunity_id, status, expires_at, used_at, source_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', '', ?, ?)
                """,
                (token, opportunity_id, status, expires_at, now, now),
            )
            row = conn.execute(
                "SELECT * FROM volunteer_confirmations WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        return dict(row) if row else {}

    def update_volunteer_confirmation(
        self,
        token: str,
        status: str,
        *,
        used_at: str = "",
        source_message_id: str = "",
    ) -> None:
        token = token.upper()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE volunteer_confirmations
                SET status = ?, used_at = COALESCE(NULLIF(?, ''), used_at),
                    source_message_id = COALESCE(NULLIF(?, ''), source_message_id),
                    updated_at = ?
                WHERE token = ?
                """,
                (status, used_at, source_message_id, utc_now(), token),
            )

    def record_decision(self, opportunity_id: str, decision: str, operator_id: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO decisions(opportunity_id, decision, operator_id, created_at) VALUES (?, ?, ?, ?)",
                (opportunity_id, decision, operator_id, utc_now()),
            )

    def set_binding(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bindings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def get_binding(self, key: str) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM bindings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def create_form_task(
        self,
        task_id: str,
        opportunity_id: str,
        signup_url: str,
        run_at: str,
        status: str = "pending",
        user_id: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO form_tasks(
                    id, opportunity_id, signup_url, run_at, status, created_at, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, opportunity_id, signup_url, run_at, status, now, now, user_id),
            )

    def due_form_tasks(self, now_iso: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM form_tasks WHERE status = 'pending' AND run_at <= ? ORDER BY run_at ASC",
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_form_task(self, task_id: str, status: str, exit_code: int | None = None, log: str = "", config_path: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE form_tasks
                SET status = ?, exit_code = ?, log = ?, config_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, exit_code, log, config_path, utc_now(), task_id),
            )

    def add_log(self, level: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO logs(level, message, payload, created_at) VALUES (?, ?, ?, ?)",
                (level, message, json.dumps(payload or {}, ensure_ascii=False), utc_now()),
            )

    def find_last_log(self, message: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM logs WHERE message = ? ORDER BY id DESC LIMIT 1",
                (message,),
            ).fetchone()
        if not row:
            return None
        record = dict(row)
        try:
            record["payload"] = json.loads(record.get("payload") or "{}")
        except json.JSONDecodeError:
            pass
        return record

    def prune_logs(self, keep_days: int = 14, max_rows: int = 5000) -> int:
        """删除过期日志并限制总行数，防止 logs 表无限增长。返回删除行数。"""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, keep_days))
        ).isoformat(timespec="seconds")
        with self.connect() as conn:
            deleted = conn.execute("DELETE FROM logs WHERE created_at < ?", (cutoff,)).rowcount
            deleted += conn.execute(
                "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)",
                (max(100, max_rows),),
            ).rowcount
        return deleted

    def record_feed_result(
        self,
        url: str,
        name: str,
        ok: bool,
        *,
        items: int = 0,
        error: str = "",
    ) -> int:
        """记录一次 feed 拉取结果，返回当前连续失败次数（成功则为 0）。"""
        now = utc_now()
        with self.connect() as conn:
            if ok:
                conn.execute(
                    """
                    INSERT INTO feed_health(
                        url, name, last_status, last_error, last_items, consecutive_failures,
                        total_ok, total_failed, last_ok_at, last_failed_at, updated_at
                    ) VALUES (?, ?, 'ok', '', ?, 0, 1, 0, ?, NULL, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        name = excluded.name,
                        last_status = 'ok',
                        last_error = '',
                        last_items = excluded.last_items,
                        consecutive_failures = 0,
                        total_ok = total_ok + 1,
                        last_ok_at = excluded.last_ok_at,
                        updated_at = excluded.updated_at
                    """,
                    (url, name, items, now, now),
                )
                return 0
            conn.execute(
                """
                INSERT INTO feed_health(
                    url, name, last_status, last_error, last_items, consecutive_failures,
                    total_ok, total_failed, last_ok_at, last_failed_at, updated_at
                ) VALUES (?, ?, 'failed', ?, 0, 1, 0, 1, NULL, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    name = excluded.name,
                    last_status = 'failed',
                    last_error = excluded.last_error,
                    consecutive_failures = consecutive_failures + 1,
                    total_failed = total_failed + 1,
                    last_failed_at = excluded.last_failed_at,
                    updated_at = excluded.updated_at
                """,
                (url, name, error[:500], now, now),
            )
            row = conn.execute(
                "SELECT consecutive_failures FROM feed_health WHERE url = ?", (url,)
            ).fetchone()
            return int(row["consecutive_failures"]) if row else 1

    def list_feed_health(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feed_health ORDER BY consecutive_failures DESC, name ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_user(
        self,
        username: str,
        password_hash: str,
        display_name: str,
        role: str = "user",
        invite_code: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        user_id = user_id or f"user_{secrets.token_urlsafe(10)}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users(id, username, password_hash, display_name, role, invite_code, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, password_hash, display_name, role, invite_code, now, now),
            )
        return self.get_user(user_id) or {}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, username, display_name, role, created_at, updated_at FROM users ORDER BY created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_users(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"] or 0)

    def ensure_invite_code(self, code: str, label: str = "", max_uses: int = 7) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO invite_codes(code, label, max_uses, uses, disabled, created_at, updated_at)
                VALUES (?, ?, ?, 0, 0, ?, ?)
                """,
                (code, label, max_uses, now, now),
            )

    def use_invite_code(self, code: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
            if not row or int(row["disabled"]) or int(row["uses"]) >= int(row["max_uses"]):
                return False
            conn.execute(
                "UPDATE invite_codes SET uses = uses + 1, updated_at = ? WHERE code = ?",
                (utc_now(), code),
            )
        return True

    def list_invite_codes(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def create_session(self, user_id: str, token: str, expires_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token, user_id, expires_at, utc_now()),
            )

    def get_session_user(self, token: str, now_iso: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ? AND sessions.expires_at > ?
                """,
                (token, now_iso),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def save_user_profile(self, user_id: str, profile: dict[str, Any]) -> None:
        now = utc_now()
        answers = profile.get("answers_json")
        if not isinstance(answers, str):
            answers = json.dumps(profile.get("answers") or [], ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles(
                    user_id, name, phone, student_id, email, college, grade, wechat,
                    answers_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = excluded.name,
                    phone = excluded.phone,
                    student_id = excluded.student_id,
                    email = excluded.email,
                    college = excluded.college,
                    grade = excluded.grade,
                    wechat = excluded.wechat,
                    answers_json = excluded.answers_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    profile.get("name", ""),
                    profile.get("phone", ""),
                    profile.get("student_id", ""),
                    profile.get("email", ""),
                    profile.get("college", ""),
                    profile.get("grade", ""),
                    profile.get("wechat", ""),
                    answers,
                    now,
                    now,
                ),
            )

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return {"user_id": user_id, "answers": []}
        data = dict(row)
        try:
            data["answers"] = json.loads(data.pop("answers_json") or "[]")
        except json.JSONDecodeError:
            data["answers"] = []
        return data

    def save_user_schedule(self, user_id: str, schedule_config: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_schedules(user_id, schedule_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    schedule_json = excluded.schedule_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, json.dumps(schedule_config, ensure_ascii=False), now, now),
            )

    def get_user_schedule(self, user_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT schedule_json FROM user_schedules WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return {"day_start": "08:00", "day_end": "22:00", "days": {}}
        try:
            parsed = json.loads(row["schedule_json"])
        except json.JSONDecodeError:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {"day_start": "08:00", "day_end": "22:00", "days": {}}

    def upsert_user_opportunity(self, payload: dict[str, Any]) -> None:
        now = utc_now()
        payload = dict(payload)
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        payload.setdefault("pushed_at", "")
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at, status, pushed_at FROM user_opportunities WHERE user_id = ? AND opportunity_id = ?",
                (payload["user_id"], payload["opportunity_id"]),
            ).fetchone()
            if existing:
                payload["created_at"] = existing["created_at"]
                payload["status"] = existing["status"]
                payload["pushed_at"] = existing["pushed_at"]
            conn.execute(
                """
                INSERT INTO user_opportunities(
                    id, user_id, opportunity_id, schedule_status, free_time_text,
                    matched_time_text, status, pushed_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :opportunity_id, :schedule_status, :free_time_text,
                    :matched_time_text, :status, :pushed_at, :created_at, :updated_at
                )
                ON CONFLICT(user_id, opportunity_id) DO UPDATE SET
                    schedule_status = excluded.schedule_status,
                    free_time_text = excluded.free_time_text,
                    matched_time_text = excluded.matched_time_text,
                    updated_at = excluded.updated_at
                """,
                payload,
            )

    def list_user_opportunities(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    user_opportunities.id AS user_opportunity_id,
                    user_opportunities.user_id,
                    user_opportunities.schedule_status AS user_schedule_status,
                    user_opportunities.free_time_text AS user_free_time_text,
                    user_opportunities.matched_time_text AS user_matched_time_text,
                    user_opportunities.status AS user_status,
                    user_opportunities.pushed_at,
                    opportunities.*
                FROM user_opportunities
                JOIN opportunities ON opportunities.id = user_opportunities.opportunity_id
                WHERE user_opportunities.user_id = ?
                ORDER BY opportunities.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user_opportunity(self, user_id: str, opportunity_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    user_opportunities.id AS user_opportunity_id,
                    user_opportunities.user_id,
                    user_opportunities.schedule_status AS user_schedule_status,
                    user_opportunities.free_time_text AS user_free_time_text,
                    user_opportunities.matched_time_text AS user_matched_time_text,
                    user_opportunities.status AS user_status,
                    user_opportunities.pushed_at,
                    opportunities.*
                FROM user_opportunities
                JOIN opportunities ON opportunities.id = user_opportunities.opportunity_id
                WHERE user_opportunities.user_id = ? AND user_opportunities.opportunity_id = ?
                """,
                (user_id, opportunity_id),
            ).fetchone()
        return dict(row) if row else None

    def update_user_opportunity_status(self, user_id: str, opportunity_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE user_opportunities SET status = ?, updated_at = ? WHERE user_id = ? AND opportunity_id = ?",
                (status, utc_now(), user_id, opportunity_id),
            )

    def mark_user_opportunity_pushed(self, user_id: str, opportunity_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE user_opportunities SET pushed_at = ?, updated_at = ? WHERE user_id = ? AND opportunity_id = ?",
                (utc_now(), utc_now(), user_id, opportunity_id),
            )

    def list_unpushed_user_opportunities(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_opportunities.user_id, user_opportunities.opportunity_id,
                       user_opportunities.schedule_status AS user_schedule_status,
                       user_opportunities.matched_time_text AS user_matched_time_text,
                       opportunities.title, opportunities.activity_time, opportunities.location,
                       opportunities.deadline
                FROM user_opportunities
                JOIN opportunities ON opportunities.id = user_opportunities.opportunity_id
                WHERE user_opportunities.pushed_at IS NULL OR user_opportunities.pushed_at = ''
                ORDER BY opportunities.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def save_push_subscription(self, user_id: str, subscription: dict[str, Any], user_agent: str = "") -> str:
        endpoint = str(subscription.get("endpoint") or "")
        if not endpoint:
            raise ValueError("push subscription 缺少 endpoint")
        subscription_id = f"push_{secrets.token_urlsafe(10)}"
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO push_subscriptions(
                    id, user_id, endpoint, subscription_json, user_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    user_id = excluded.user_id,
                    subscription_json = excluded.subscription_json,
                    user_agent = excluded.user_agent,
                    updated_at = excluded.updated_at
                """,
                (subscription_id, user_id, endpoint, json.dumps(subscription, ensure_ascii=False), user_agent, now, now),
            )
        return subscription_id

    def list_push_subscriptions(self, user_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM push_subscriptions"
        params: tuple[Any, ...] = ()
        if user_id:
            sql += " WHERE user_id = ?"
            params = (user_id,)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            try:
                data["subscription"] = json.loads(data.get("subscription_json") or "{}")
            except json.JSONDecodeError:
                data["subscription"] = {}
            result.append(data)
        return result

    def update_push_result(self, subscription_id: str, ok: bool, error: str = "") -> None:
        with self.connect() as conn:
            if ok:
                conn.execute(
                    "UPDATE push_subscriptions SET last_success_at = ?, last_error = '', updated_at = ? WHERE id = ?",
                    (utc_now(), utc_now(), subscription_id),
                )
            else:
                conn.execute(
                    "UPDATE push_subscriptions SET last_error = ?, updated_at = ? WHERE id = ?",
                    (error[:500], utc_now(), subscription_id),
                )

    def delete_push_subscription(self, endpoint: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def list_feed_configs(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feed_configs"
        params: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def add_feed_config(self, name: str, url: str, enabled: bool = True, feed_id: str = "") -> dict[str, Any]:
        now = utc_now()
        feed_id = feed_id or f"feed_{secrets.token_urlsafe(8)}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feed_configs(id, name, url, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (feed_id, name, url, 1 if enabled else 0, now, now),
            )
        return {"id": feed_id, "name": name, "url": url, "enabled": enabled}

    def update_feed_config(self, feed_id: str, name: str, url: str, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE feed_configs SET name = ?, url = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (name, url, 1 if enabled else 0, utc_now(), feed_id),
            )

    def delete_feed_config(self, feed_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM feed_configs WHERE id = ?", (feed_id,))

    def recent_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_calendar_sync(self, opportunity_id: str, user_id: str = "") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_syncs WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def upsert_calendar_sync(
        self,
        opportunity_id: str,
        user_id: str,
        status: str,
        *,
        calendar_event_id: str = "",
        error: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        sync_id = f"{user_id or 'single'}:{opportunity_id}"
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM calendar_syncs WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO calendar_syncs(
                    id, opportunity_id, user_id, calendar_event_id, status, error,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id, user_id) DO UPDATE SET
                    calendar_event_id = excluded.calendar_event_id,
                    status = excluded.status,
                    error = excluded.error,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    sync_id,
                    opportunity_id,
                    user_id,
                    calendar_event_id,
                    status,
                    error[:1000],
                    json.dumps(payload or {}, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )

    def list_calendar_syncs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_syncs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_schedule_inbox_sync(self, opportunity_id: str, user_id: str = "") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_inbox_syncs WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def upsert_schedule_inbox_sync(
        self,
        opportunity_id: str,
        user_id: str,
        status: str,
        *,
        inbox_item_id: str = "",
        error: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        sync_id = f"{user_id or 'single'}:{opportunity_id}"
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM schedule_inbox_syncs WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO schedule_inbox_syncs(
                    id, opportunity_id, user_id, inbox_item_id, status, error,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(opportunity_id, user_id) DO UPDATE SET
                    inbox_item_id = excluded.inbox_item_id,
                    status = excluded.status,
                    error = excluded.error,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    sync_id,
                    opportunity_id,
                    user_id,
                    inbox_item_id,
                    status,
                    error[:1000],
                    json.dumps(payload or {}, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )

    def list_schedule_inbox_syncs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_inbox_syncs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
