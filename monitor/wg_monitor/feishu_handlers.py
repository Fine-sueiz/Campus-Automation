from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .calendar_sync import sync_opportunity_to_calendar
from .db import MonitorDB, utc_now
from .feishu import (
    FeishuSettings,
    callback_toast,
    extract_card_action,
    extract_message_event,
)


class FeishuTextClient(Protocol):
    def send_text(self, chat_id: str, text: str) -> str:
        ...


def bound_chat_id(db: MonitorDB) -> str:
    return db.get_binding("default_chat_id") or FeishuSettings.from_env().default_chat_id


def send_text_safe(client: FeishuTextClient, chat_id: str, text: str) -> None:
    if not chat_id or not FeishuSettings.from_env().enabled:
        return
    try:
        client.send_text(chat_id, text)
    except Exception:
        return


def handle_binding_message(db: MonitorDB, client: FeishuTextClient, payload: dict[str, Any]) -> dict[str, Any]:
    chat_id, message_type, text = extract_message_event(payload)
    if message_type == "text" and "绑定" in text and chat_id:
        db.set_binding("default_chat_id", chat_id)
        send_text_safe(client, chat_id, "已绑定这个私人群聊，以后校园机会会推送到这里。")
        return {"ok": True, "bound_chat_id": chat_id}
    return {"ok": True}


def handle_card_decision(
    root: Path,
    db: MonitorDB,
    client: FeishuTextClient,
    payload: dict[str, Any],
    *,
    after_join: Callable[[], None] | None = None,
    app_config: dict[str, Any] | None = None,
    runner_label: str = "本地程序",
) -> dict[str, Any]:
    opportunity_id, decision, operator_id = extract_card_action(payload)
    if not opportunity_id:
        return callback_toast("没有识别到机会 ID", "error")

    opportunity = db.get_opportunity(opportunity_id)
    if not opportunity:
        return callback_toast("这条机会不在数据库中", "error")

    db.record_decision(opportunity_id, decision, operator_id)
    chat_id = bound_chat_id(db)

    if decision == "join":
        if not opportunity.get("signup_url"):
            db.update_opportunity_status(opportunity_id, "need_human")
            send_text_safe(client, chat_id, f"已记录参加，但未识别到报名链接：{opportunity['title']}")
            return callback_toast("已记录参加，但需要人工处理报名链接", "warning")

        task_id = f"task-{opportunity_id}"
        db.create_form_task(task_id, opportunity_id, str(opportunity["signup_url"]), utc_now())
        db.update_opportunity_status(opportunity_id, "approved")
        if app_config is not None:
            try:
                sync_opportunity_to_calendar(db, app_config, opportunity)
            except Exception as exc:  # noqa: BLE001
                db.add_log("error", f"calendar sync after card decision failed: {exc}", {"opportunity_id": opportunity_id})
        send_text_safe(client, chat_id, f"已记录参加：{opportunity['title']}。{runner_label}会开始报名。")
        if after_join:
            after_join()
        return callback_toast("已记录参加，报名任务已创建")

    if decision == "reject":
        db.update_opportunity_status(opportunity_id, "rejected")
        send_text_safe(client, chat_id, f"已记录不参加：{opportunity['title']}")
        return callback_toast("已记录不参加")

    db.update_opportunity_status(opportunity_id, "need_human")
    send_text_safe(client, chat_id, f"已标记需要人工查看：{opportunity['title']}\n{opportunity['article_url']}")
    return callback_toast("已标记需要人工查看", "warning")
