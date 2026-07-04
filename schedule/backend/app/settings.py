from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_API_KEY = "dev-schedule-key"


def get_db_path() -> Path:
    value = os.getenv("SCHEDULE_DB_PATH")
    if value:
        return Path(value)
    return PROJECT_ROOT / "data" / "schedule.db"


def get_api_key() -> str:
    return os.getenv("SCHEDULE_API_KEY", DEFAULT_API_KEY)


def get_schedule_api_base() -> str:
    return os.getenv("SCHEDULE_API_BASE", "http://127.0.0.1:8000").rstrip("/")


def get_cors_origins() -> list[str]:
    raw = os.getenv(
        "SCHEDULE_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_qq_sync_config_path() -> Path:
    value = os.getenv("QQ_SYNC_CONFIG_PATH")
    if value:
        return Path(value)
    return PROJECT_ROOT / "data" / "qq_sync_config.json"


def get_monitor_api_base() -> str:
    return os.getenv("MONITOR_API_BASE", "http://127.0.0.1:8011").rstrip("/")


def get_monitor_integration_key() -> str:
    return os.getenv("MONITOR_INTEGRATION_KEY", DEFAULT_API_KEY)
