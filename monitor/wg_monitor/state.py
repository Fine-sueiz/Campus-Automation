"""已退役：CLI 旧去重状态（data/state.json）。

流水线统一后（见 pipeline.py），CLI/GUI/服务端的去重一律使用
SQLite articles 表。本模块仅为兼容旧版打包 exe 保留，新代码不要引用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MonitorState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"seen": {}, "sent": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data.setdefault("seen", {})
        self.data.setdefault("sent", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.data.get("seen", {})

    def is_sent(self, item_id: str) -> bool:
        return item_id in self.data.get("sent", {})

    def mark_seen(self, item_id: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("seen_at", now_iso())
        self.data.setdefault("seen", {})[item_id] = payload

    def mark_sent(self, item_id: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("sent_at", now_iso())
        self.data.setdefault("sent", {})[item_id] = payload


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
