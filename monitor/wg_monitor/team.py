from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request, Response

from .db import MonitorDB, utc_now
from .schedule import DAY_ALIASES, DAY_ORDER


SESSION_COOKIE = "campus_session"
DEFAULT_INVITE_CODE = "TEAM2026"


def password_hash(password: str, salt: str | None = None) -> str:
    salt_bytes = base64.urlsafe_b64decode(salt.encode("ascii")) if salt else secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 120_000)
    return (
        base64.urlsafe_b64encode(salt_bytes).decode("ascii")
        + "$"
        + base64.urlsafe_b64encode(derived).decode("ascii")
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    calculated = password_hash(password, salt).split("$", 1)[1]
    return hmac.compare_digest(calculated, expected)


def session_expiry(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


def create_login_session(db: MonitorDB, response: Response, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(user_id, token, session_expiry())
    secure_cookie = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=30 * 24 * 3600,
    )
    return token


def clear_login_session(db: MonitorDB, response: Response, token: str = "") -> None:
    if token:
        db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE)


def current_user(request: Request, campus_session: str = Cookie(default="")) -> dict[str, Any]:
    db: MonitorDB = request.app.state.db
    if not campus_session:
        raise HTTPException(status_code=401, detail="not logged in")
    user = db.get_session_user(campus_session, utc_now())
    if not user:
        raise HTTPException(status_code=401, detail="session expired")
    return user


def current_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name"),
        "role": user.get("role"),
    }


def ensure_team_bootstrap(db: MonitorDB) -> None:
    db.ensure_invite_code(DEFAULT_INVITE_CODE, "默认七人邀请码", 7)
    if db.count_users() > 0:
        return
    username = os.getenv("TEAM_ADMIN_USERNAME", "admin")
    password = os.getenv("TEAM_ADMIN_PASSWORD", "admin123456")
    display_name = os.getenv("TEAM_ADMIN_DISPLAY_NAME", "管理员")
    db.create_user(
        username=username,
        password_hash=password_hash(password),
        display_name=display_name,
        role="admin",
        invite_code="bootstrap",
        user_id="admin",
    )


def parse_busy_text(text: str, day_start: str = "08:00", day_end: str = "22:00") -> dict[str, Any]:
    days = {day: {"busy": []} for day, _label in DAY_ORDER}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"课表行格式应为：周一 08:00-09:40 课程名，目前是：{line}")
        day = DAY_ALIASES.get(parts[0])
        if not day:
            raise ValueError(f"无法识别星期：{parts[0]}")
        if "-" not in parts[1]:
            raise ValueError(f"时间段需要类似 08:00-09:40，目前是：{parts[1]}")
        start, end = parts[1].split("-", 1)
        label = " ".join(parts[2:]) if len(parts) > 2 else "忙碌"
        days[day]["busy"].append({"start": start, "end": end, "name": label})
    return {"day_start": day_start, "day_end": day_end, "days": days}


def schedule_to_busy_text(schedule_config: dict[str, Any]) -> str:
    labels = dict(DAY_ORDER)
    lines: list[str] = []
    days = schedule_config.get("days") or {}
    for day, _label in DAY_ORDER:
        for item in (days.get(day) or {}).get("busy") or []:
            lines.append(
                f"{labels.get(day, day)} {item.get('start', '')}-{item.get('end', '')} {item.get('name', '忙碌')}"
            )
    return "\n".join(lines)


def user_app_config(app_config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    merged = dict(app_config)
    merged["user"] = {
        "name": profile.get("name", ""),
        "phone": profile.get("phone", ""),
        "student_id": profile.get("student_id", ""),
        "contact_email": profile.get("email", ""),
        "major": profile.get("college", ""),
    }
    merged["questionnaire_profile"] = {
        "wechat": profile.get("wechat", ""),
        "college": profile.get("college", ""),
        "grade": profile.get("grade", ""),
    }
    answers = profile.get("answers") or []
    if answers:
        merged.setdefault("questionnaire_profile", {})["answers"] = answers
    return merged
