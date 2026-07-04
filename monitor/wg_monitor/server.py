from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .config import ProjectPaths, load_env_file
from .db import MonitorDB, default_db_path
from .feed import (
    configured_wechat2rss_feed_configs,
    discover_wechat2rss_feeds,
    subscribe_wechat2rss_account_id,
    subscribe_wechat2rss_article_url,
    wechat2rss_settings,
    wechat2rss_url,
)
from .feishu import (
    FeishuClient,
    FeishuError,
    FeishuSettings,
    decrypt_payload_if_needed,
    event_type,
    is_url_verification,
    verify_callback_token,
)
from .feishu_handlers import handle_binding_message, handle_card_decision
from .form_runner import run_form_task, run_user_form_task
from .opportunity import CATEGORY_LABELS
from .pipeline import maybe_auto_send_opportunity_email, scan_cycle  # noqa: F401  测试从 server 导入 maybe_auto_send_opportunity_email
from .settings import load_project_cached
from .team_server import register_team_routes
from .volunteer import poll_mail_triggers, send_volunteer_status_notice
from .wechat_integration import (
    WechatWatcherSettings,
    process_wechat_items,
    record_watcher_status,
    wechat_watcher_status,
)


def db_path_for(root: Path) -> Path:
    return default_db_path(root)


def create_app(root: Path | str = ".", start_background: bool | None = None) -> FastAPI:
    root_path = Path(root).resolve()
    paths = ProjectPaths.from_root(root_path)
    load_env_file(paths.env_file)
    db = MonitorDB(db_path_for(root_path))
    feishu_client = FeishuClient()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            app.state.background_task = asyncio.create_task(
                background_loop(root_path, db, feishu_client, app.state.stop_event)
            )
        try:
            yield
        finally:
            app.state.stop_event.set()
            task = app.state.background_task
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Campus Opportunity Monitor", version="0.2.0", lifespan=lifespan)
    app.state.root = root_path
    app.state.db = db
    app.state.feishu = feishu_client
    app.state.stop_event = asyncio.Event()
    app.state.background_task = None
    register_team_routes(app, root_path, db)

    if start_background is None:
        start_background = os.getenv("RUN_BACKGROUND", "true").lower() not in {"0", "false", "no"}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        chat_id = db.get_binding("default_chat_id") or FeishuSettings.from_env().default_chat_id
        last_scan = db.find_last_log("scan cycle completed")
        return {
            "ok": True,
            "root": str(root_path),
            "db": str(db_path_for(root_path)),
            "feishu_configured": FeishuSettings.from_env().enabled,
            "chat_bound": bool(chat_id),
            "last_scan": {
                "at": last_scan.get("created_at") if last_scan else None,
                "summary": last_scan.get("payload") if last_scan else None,
            },
            "feeds": db.list_feed_health(),
        }

    @app.post("/feishu/events")
    async def feishu_events(request: Request) -> dict[str, Any]:
        payload = await parse_feishu_request(request)
        if is_url_verification(payload):
            return {"challenge": payload.get("challenge")}
        verify_callback_token(payload, FeishuSettings.from_env().verification_token)

        if event_type(payload) == "im.message.receive_v1":
            result = await asyncio.to_thread(handle_binding_message, db, feishu_client, payload)
            if result.get("bound_chat_id"):
                return result
        return {"ok": True}

    @app.post("/feishu/card-action")
    async def feishu_card_action(request: Request) -> dict[str, Any]:
        payload = await parse_feishu_request(request)
        verify_callback_token(payload, FeishuSettings.from_env().verification_token)
        _paths, app_config, _schedule_config = load_project_cached(root_path)
        return await asyncio.to_thread(
            handle_card_decision,
            root_path,
            db,
            feishu_client,
            payload,
            app_config=app_config,
            runner_label="服务器",
        )

    @app.post("/admin/scan-once")
    async def admin_scan_once() -> dict[str, Any]:
        return await asyncio.to_thread(scan_once, root_path, db, feishu_client)

    @app.post("/admin/run-due-tasks")
    async def admin_run_due_tasks() -> dict[str, Any]:
        return await asyncio.to_thread(run_due_form_tasks, root_path, db, feishu_client)

    @app.post("/admin/poll-mail-triggers")
    async def admin_poll_mail_triggers() -> dict[str, Any]:
        paths, app_config, _schedule_config = load_project_cached(root_path)
        return await asyncio.to_thread(poll_mail_triggers, db, app_config)

    @app.get("/admin/wechat2rss/status")
    async def admin_wechat2rss_status() -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root_path)
        settings = wechat2rss_settings(app_config)
        if not bool(settings.get("enabled", False)):
            return {"enabled": False, "reachable": False, "feeds": [], "error": ""}
        try:
            feeds = await asyncio.to_thread(
                discover_wechat2rss_feeds,
                app_config,
                int((app_config.get("monitor") or {}).get("article_timeout_seconds", 15)),
            )
            return {
                "enabled": True,
                "reachable": True,
                "base_url": settings.get("base_url"),
                "list_url": wechat2rss_url(app_config, "/list"),
                "feeds": feeds,
                "configured_feeds": configured_wechat2rss_feed_configs(app_config),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "enabled": True,
                "reachable": False,
                "base_url": settings.get("base_url"),
                "list_url": wechat2rss_url(app_config, "/list"),
                "feeds": configured_wechat2rss_feed_configs(app_config),
                "error": str(exc),
            }

    @app.post("/admin/wechat2rss/subscribe")
    async def admin_wechat2rss_subscribe(request: Request) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root_path)
        settings = wechat2rss_settings(app_config)
        if not bool(settings.get("enabled", False)):
            raise HTTPException(status_code=400, detail="wechat2rss disabled")
        payload = await request.json()
        article_urls = payload.get("article_urls") or payload.get("urls") or []
        account_ids = payload.get("account_ids") or payload.get("ids") or []
        if isinstance(article_urls, str):
            article_urls = [article_urls]
        if isinstance(account_ids, str):
            account_ids = [account_ids]
        timeout = int((app_config.get("monitor") or {}).get("article_timeout_seconds", 15))
        results: list[dict[str, Any]] = []
        for article_url in [str(item).strip() for item in article_urls if str(item).strip()]:
            try:
                response_text = await asyncio.to_thread(
                    subscribe_wechat2rss_article_url,
                    app_config,
                    article_url,
                    timeout,
                )
                results.append({"type": "article_url", "value": article_url, "status": "ok", "response": response_text[:500]})
            except Exception as exc:  # noqa: BLE001
                results.append({"type": "article_url", "value": article_url, "status": "failed", "error": str(exc)})
        for account_id in [str(item).strip() for item in account_ids if str(item).strip()]:
            try:
                response_text = await asyncio.to_thread(
                    subscribe_wechat2rss_account_id,
                    app_config,
                    account_id,
                    timeout,
                )
                results.append({"type": "account_id", "value": account_id, "status": "ok", "response": response_text[:500]})
            except Exception as exc:  # noqa: BLE001
                results.append({"type": "account_id", "value": account_id, "status": "failed", "error": str(exc)})
        return {"ok": all(item["status"] == "ok" for item in results), "results": results}

    @app.post("/api/integrations/wechat/articles")
    async def ingest_wechat_articles(request: Request) -> dict[str, Any]:
        _paths, app_config, schedule_config = load_project_cached(root_path)
        settings = WechatWatcherSettings.from_config(app_config)
        if request.headers.get("X-Integration-Key") != settings.integration_key:
            raise HTTPException(status_code=401, detail="invalid integration key")
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="object payload required")
        watcher = payload.get("watcher") or {}
        if isinstance(watcher, dict):
            record_watcher_status(db, watcher)
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items must be a list")
        force = bool(payload.get("force", False))
        return await asyncio.to_thread(
            process_wechat_items,
            db,
            app_config,
            schedule_config,
            [item for item in items if isinstance(item, dict)],
            force=force,
        )

    @app.get("/api/integrations/wechat/status")
    async def get_wechat_watcher_status(request: Request) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root_path)
        settings = WechatWatcherSettings.from_config(app_config)
        if request.headers.get("X-Integration-Key") != settings.integration_key:
            raise HTTPException(status_code=401, detail="invalid integration key")
        return wechat_watcher_status(db, app_config)

    # 注意：/api/opportunities 已被冻结的 team PWA 占用（cookie 登录、按用户物化），
    # 本接口是全量库的只读查询，放 /admin 命名空间（本机内环、无鉴权，同 /admin/scan-once）
    @app.get("/admin/opportunities")
    async def admin_list_opportunities(
        since: str | None = None,
        min_score: int | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        since_utc: str | None = None
        if since:
            try:
                since_utc = normalize_since(since)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid since, expected ISO 8601") from exc
        limit = max(1, min(limit, 1000))
        rows = await asyncio.to_thread(
            lambda: db.list_opportunities(
                since=since_utc, min_score=min_score, status=status, limit=limit
            )
        )
        return {"items": [public_opportunity(row) for row in rows], "count": len(rows)}

    return app


# /api/opportunities 对外暴露的字段白名单（排除 raw_text 等大字段与内部字段）
OPPORTUNITY_PUBLIC_FIELDS = (
    "id",
    "title",
    "category",
    "score",
    "score_reasons",
    "deadline",
    "activity_time",
    "location",
    "signup_url",
    "article_url",
    "schedule_status",
    "status",
    "source_name",
    "created_at",
    "updated_at",
)


def public_opportunity(row: dict[str, Any]) -> dict[str, Any]:
    item = {key: row.get(key) for key in OPPORTUNITY_PUBLIC_FIELDS}
    category = str(row.get("category") or "")
    item["category_label"] = CATEGORY_LABELS.get(category, category)
    return item


def normalize_since(value: str) -> str:
    """把任意 ISO 8601 输入归一到 utc_now() 的格式；无时区按 UTC 解释。"""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


async def parse_feishu_request(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid json") from exc
    try:
        return decrypt_payload_if_needed(payload, FeishuSettings.from_env().encrypt_key)
    except FeishuError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def background_loop(root: Path, db: MonitorDB, feishu_client: FeishuClient, stop_event: asyncio.Event) -> None:
    next_scan_at = 0.0
    next_mail_at = 0.0
    scan_interval = 300
    mail_interval = 60
    while not stop_event.is_set():
        try:
            now = time.monotonic()
            did_work = False
            scan_summary: dict[str, Any] | None = None
            mail_summary: dict[str, Any] | None = None

            if now >= next_scan_at:
                scan_summary = await asyncio.to_thread(scan_once, root, db, feishu_client)
                scan_interval = int(scan_summary.get("interval", scan_interval))
                next_scan_at = now + max(30, scan_interval)
                did_work = True

            if now >= next_mail_at:
                _paths, app_config, _schedule_config = await asyncio.to_thread(load_project_cached, root)
                mail_summary = await asyncio.to_thread(poll_mail_triggers, db, app_config)
                mail_interval = int(mail_summary.get("interval", mail_interval))
                next_mail_at = now + max(15, mail_interval)
                did_work = True

            if did_work:
                task_summary = await asyncio.to_thread(run_due_form_tasks, root, db, feishu_client)
                payload: dict[str, Any] = {"tasks": task_summary}
                if scan_summary is not None:
                    payload["scan"] = scan_summary
                if mail_summary is not None:
                    payload["mail_triggers"] = mail_summary
                # 扫描摘要由 pipeline 以 "scan cycle completed" 记录，这里只记后台周期聚合
                db.add_log("info", "background cycle completed", payload)
        except Exception as exc:  # noqa: BLE001
            db.add_log("error", f"background loop failed: {exc}")
            next_scan_at = time.monotonic() + 60
            next_mail_at = time.monotonic() + 60
        try:
            sleep_until = min(next_scan_at, next_mail_at)
            timeout = max(5, sleep_until - time.monotonic())
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


def scan_once(root: Path, db: MonitorDB, feishu_client: FeishuClient) -> dict[str, Any]:
    """一轮完整扫描。实现在 pipeline.scan_cycle，此处保留入口名供端点与 local_feishu 使用。"""
    return scan_cycle(root, db, feishu_client)


def run_due_form_tasks(root: Path, db: MonitorDB, feishu_client: FeishuClient) -> dict[str, Any]:
    paths, app_config, _schedule_config = load_project_cached(root)
    tasks = db.due_form_tasks(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    counts = {"due": len(tasks), "submitted": 0, "need_human": 0, "failed": 0}
    chat_id = db.get_binding("default_chat_id") or FeishuSettings.from_env().default_chat_id
    for task in tasks:
        opportunity = db.get_opportunity(str(task["opportunity_id"]))
        if not opportunity:
            db.update_form_task(str(task["id"]), "failed", exit_code=404, log="opportunity not found")
            counts["failed"] += 1
            continue
        db.update_form_task(str(task["id"]), "submitting")
        task_user_id = str(task.get("user_id") or "")
        if task_user_id:
            profile = db.get_user_profile(task_user_id)
            result = run_user_form_task(root, app_config, opportunity, str(task["id"]), profile)
        else:
            result = run_form_task(root, app_config, opportunity, str(task["id"]))
        db.update_form_task(str(task["id"]), result.status, result.exit_code, result.log, result.config_path)
        db.update_opportunity_status(str(task["opportunity_id"]), result.status)
        if task_user_id:
            db.update_user_opportunity_status(task_user_id, str(task["opportunity_id"]), result.status)
        counts[result.status] = counts.get(result.status, 0) + 1
        notify = f"报名任务结果：{opportunity['title']}\n状态：{result.status}\n退出码：{result.exit_code}"
        if result.status == "need_human":
            notify += "\n需要人工处理，可能是验证码、登录、缺字段或提交按钮未找到。"
        if opportunity.get("category") == "volunteer" and result.status in {"need_human", "failed"}:
            send_volunteer_status_notice(app_config, notify)
        try:
            if chat_id and FeishuSettings.from_env().enabled:
                feishu_client.send_text(chat_id, notify)
        except Exception:
            pass
    return counts
