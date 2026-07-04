from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import utc_now


@dataclass(frozen=True)
class FormRunResult:
    status: str
    exit_code: int
    log: str
    config_path: str


def helper_dir(root: Path, app_config: dict[str, Any] | None = None) -> Path:
    configured = os.getenv("QUESTIONNAIRE_HELPER_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    configured = str(((app_config or {}).get("form_runner") or {}).get("helper_dir") or "").strip()
    if configured:
        path = Path(configured)
        return path.resolve() if path.is_absolute() else (root / path).resolve()
    return (root / "questionnaire_helper").resolve()


def load_template(helper: Path) -> dict[str, Any]:
    template_path = helper / "config.json"
    if template_path.exists():
        return json.loads(template_path.read_text(encoding="utf-8-sig"))
    return {
        "timezone": "Asia/Shanghai",
        "auto_submit": True,
        "continue_on_fill_error": True,
        "submit_delay_seconds": 0.5,
        "close_delay_seconds": 2,
        "manual_review_seconds": 3600,
        "auto_agree_terms": True,
        "headless": True,
        "profile_fields": [],
        "answers": [],
        "simple_validation_rules": [],
        "llm_validation": {"enabled": False},
    }


def build_questionnaire_config(
    root: Path,
    app_config: dict[str, Any],
    opportunity: dict[str, Any],
    task_id: str,
) -> Path:
    helper = helper_dir(root, app_config)
    config = load_template(helper)
    profile = app_config.get("questionnaire_profile") or {}
    user = app_config.get("user") or {}
    start_time = str(opportunity.get("submit_at") or "").strip()

    config["questionnaires"] = [
        {
            "label": str(opportunity.get("title") or "校园机会报名"),
            "url": str(opportunity.get("signup_url") or ""),
            **({"start_time": start_time} if start_time else {}),
        }
    ]
    config["timezone"] = "Asia/Shanghai"
    config["auto_submit"] = bool((app_config.get("form_runner") or {}).get("auto_submit", True))
    config["headless"] = bool((app_config.get("form_runner") or {}).get("headless", True))
    config["profile_fields"] = [
        {"label": "姓名", "keywords": ["姓名", "名字", "请输入你的名字", "请输入姓名"], "value": user.get("name") or profile.get("name", "")},
        {"label": "学号", "keywords": ["学号", "学生编号", "学生证号", "请输入你的学号"], "value": user.get("student_id") or profile.get("student_id", "")},
        {"label": "手机号", "keywords": ["手机号", "手机号码", "联系电话", "联系方式"], "value": user.get("phone") or profile.get("phone", "")},
        {"label": "微信号", "keywords": ["微信号", "微信", "请输入你的微信号"], "value": profile.get("wechat", "")},
    ]
    config["profile_fields"] = [item for item in config["profile_fields"] if item["value"]]
    config["answers"] = [
        {
            "label": "学院",
            "keywords": ["学院", "院系", "所在学院", "请选择你的学院"],
            "type": "single",
            "value": profile.get("college") or user.get("major", ""),
            "required": False,
        },
        {
            "label": "年级",
            "keywords": ["年级", "入学年级", "请选择你的年级"],
            "type": "single",
            "value": profile.get("grade", ""),
            "required": False,
        },
        {
            "label": "是否参加",
            "keywords": ["是否参加", "是否报名参加", "是否愿意参加"],
            "type": "single",
            "value": "参加",
            "required": True,
        },
        {
            "label": "活动名称",
            "keywords": ["活动名称", "报名活动", "参加活动"],
            "type": "text",
            "value": opportunity.get("title", ""),
            "required": False,
        },
    ]
    configured_answers = (app_config.get("questionnaire_profile") or {}).get("answers") or []
    if isinstance(configured_answers, list):
        config["answers"].extend(item for item in configured_answers if isinstance(item, dict))
    config["answers"] = [item for item in config["answers"] if item.get("value")]

    task_dir = root / "data" / "form_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    config_path = task_dir / f"{task_id}.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def run_form_task(root: Path, app_config: dict[str, Any], opportunity: dict[str, Any], task_id: str) -> FormRunResult:
    mode = os.getenv("FORM_RUNNER_MODE", "real").strip().lower()
    config_path = build_questionnaire_config(root, app_config, opportunity, task_id)
    if mode == "fake":
        return FormRunResult("submitted", 0, f"[fake] submitted at {utc_now()}", str(config_path))

    helper = helper_dir(root, app_config)
    script = helper / "fill_questionnaire.py"
    if not script.exists():
        return FormRunResult(
            "need_human",
            127,
            f"问卷助手不存在：{script}。请把 questionnaire_helper 挂载到服务器 /app/questionnaire_helper。",
            str(config_path),
        )

    env = os.environ.copy()
    timeout = int((app_config.get("form_runner") or {}).get("timeout_seconds", 7200))
    process = subprocess.run(
        [sys.executable, str(script), "--config", str(config_path)],
        cwd=str(helper),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    log = (process.stdout or "") + ("\n" + process.stderr if process.stderr else "")
    if process.returncode == 0:
        status = "submitted"
    elif process.returncode in {2, 3, 4, 5, 6}:
        status = "need_human"
    else:
        status = "failed"
    return FormRunResult(status, int(process.returncode), log[-12000:], str(config_path))


def run_user_form_task(
    root: Path,
    app_config: dict[str, Any],
    opportunity: dict[str, Any],
    task_id: str,
    profile: dict[str, Any],
) -> FormRunResult:
    from .team import user_app_config

    return run_form_task(root, user_app_config(app_config, profile), opportunity, task_id)
