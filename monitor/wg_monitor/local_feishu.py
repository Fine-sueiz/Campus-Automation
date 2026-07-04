from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, ProjectPaths, load_env_file, load_project
from .db import MonitorDB
from .feishu import FeishuClient, FeishuSettings, opportunity_card
from .feishu_handlers import bound_chat_id, handle_binding_message, handle_card_decision
from .server import db_path_for, run_due_form_tasks, scan_once


@dataclass(frozen=True)
class LocalFeishuOptions:
    once: bool = False
    bind_only: bool = False
    test_card: bool = False
    interval: int = 0
    run_tasks_after_join: bool = True


def sdk_payload(data: Any) -> dict[str, Any]:
    import lark_oapi as lark

    raw = lark.JSON.marshal(data)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def require_lark_sdk() -> None:
    try:
        import lark_oapi  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ConfigError("缺少飞书官方 SDK，请运行：python -m pip install -r requirements.txt") from exc


def start_due_task_thread(root: Path, db: MonitorDB, client: FeishuClient) -> None:
    def runner() -> None:
        try:
            run_due_form_tasks(root, db, client)
        except Exception as exc:  # noqa: BLE001
            db.add_log("error", f"local due form task failed: {exc}")

    threading.Thread(target=runner, daemon=True, name="local-feishu-form-runner").start()


def scan_loop(root: Path, db: MonitorDB, client: FeishuClient, stop_event: threading.Event, interval_override: int = 0) -> None:
    while not stop_event.is_set():
        try:
            summary = scan_once(root, db, client)
            run_due_form_tasks(root, db, client)
            interval = interval_override or int(summary.get("interval", 300))
            print(
                "扫描完成："
                f"feed={summary.get('feeds', 0)} "
                f"文章={summary.get('items', 0)} "
                f"新文章={summary.get('new_articles', 0)} "
                f"机会={summary.get('opportunities', 0)} "
                f"飞书卡片={summary.get('sent_cards', 0)}"
            )
        except Exception as exc:  # noqa: BLE001
            db.add_log("error", f"local scan loop failed: {exc}")
            print(f"扫描失败：{exc}")
            interval = interval_override or 60
        stop_event.wait(max(30, interval))


def send_test_card(root: Path, db: MonitorDB, client: FeishuClient) -> str:
    chat_id = bound_chat_id(db)
    if not chat_id:
        raise ConfigError("还没有绑定飞书群聊。先运行 local-feishu，在群里发送“绑定”。")

    payload = {
        "id": "local-feishu-test",
        "article_item_id": "local-feishu-test",
        "category": "volunteer",
        "category_label": "志愿活动",
        "title": "本地飞书 MVP 测试机会",
        "source_name": "本地测试",
        "article_url": "https://example.com/local-feishu-test",
        "signup_url": "https://v.wjx.cn/vm/test.aspx",
        "activity_time": "周三 14:00-17:00",
        "deadline": "",
        "location": "校内测试地点",
        "schedule_status": "available",
        "free_time_text": "",
        "matched_time_text": "周三 14:00-17:00",
        "raw_text": "本地飞书长连接测试卡片",
        "status": "pending_decision",
        "feishu_message_id": "",
    }
    db.upsert_opportunity(payload)
    message_id = client.send_message(chat_id, opportunity_card(payload))
    db.update_opportunity_status(payload["id"], "pending_decision", message_id)
    return message_id


def build_event_handler(root: Path, db: MonitorDB, client: FeishuClient, run_tasks_after_join: bool):
    require_lark_sdk()

    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    def on_message(data: Any) -> None:
        payload = sdk_payload(data)
        result = handle_binding_message(db, client, payload)
        if result.get("bound_chat_id"):
            print(f"飞书群聊已绑定：{result['bound_chat_id']}")

    def on_card_action(data: Any) -> P2CardActionTriggerResponse:
        payload = sdk_payload(data)
        toast = handle_card_decision(
            root,
            db,
            client,
            payload,
            after_join=(
                lambda: start_due_task_thread(root, db, client)
                if run_tasks_after_join
                else None
            ),
            runner_label="本地程序",
        )
        return P2CardActionTriggerResponse(toast)

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )


def start_long_connection(root: Path, db: MonitorDB, client: FeishuClient, run_tasks_after_join: bool) -> None:
    require_lark_sdk()

    import lark_oapi as lark

    settings = FeishuSettings.from_env()
    if not settings.enabled:
        raise ConfigError("请先在 .env 配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
    event_handler = build_event_handler(root, db, client, run_tasks_after_join)
    ws_client = lark.ws.Client(
        settings.app_id,
        settings.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    print("飞书长连接启动中。把机器人拉进私人群聊后，发送“绑定”。")
    ws_client.start()


def run_local_feishu(root: Path | str = ".", options: LocalFeishuOptions | None = None) -> int:
    root_path = Path(root).resolve()
    options = options or LocalFeishuOptions()
    paths = ProjectPaths.from_root(root_path)
    load_env_file(paths.env_file)
    load_project(root_path)

    if not FeishuSettings.from_env().enabled:
        raise ConfigError("请先在 .env 配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")

    db = MonitorDB(db_path_for(root_path))
    client = FeishuClient()

    if options.test_card:
        message_id = send_test_card(root_path, db, client)
        print(f"测试卡片已发送：{message_id}")
        return 0

    if options.once:
        summary = scan_once(root_path, db, client)
        run_due_form_tasks(root_path, db, client)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    stop_event = threading.Event()
    scan_thread: threading.Thread | None = None
    if not options.bind_only:
        scan_thread = threading.Thread(
            target=scan_loop,
            args=(root_path, db, client, stop_event, options.interval),
            daemon=True,
            name="local-feishu-scan-loop",
        )
        scan_thread.start()

    try:
        start_long_connection(root_path, db, client, options.run_tasks_after_join)
    finally:
        stop_event.set()
        if scan_thread:
            scan_thread.join(timeout=3)
    return 0
