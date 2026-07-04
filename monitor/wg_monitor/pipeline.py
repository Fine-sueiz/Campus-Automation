"""统一扫描流水线。

服务端（server.scan_once）、CLI（monitor.run_once）、GUI 共用同一条流水线：

    收集 feed → 拉取条目 → 去重 → 正文抓取 → 机会/志愿分析 → 动作路由

动作（飞书卡片、自动投递邮件、按用户物化、日程收件箱、志愿提醒、Web 推送）
各自带开关，单条文章处理失败不会中断整轮扫描。
去重只认 SQLite（articles 表），data/state.json 已退役。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .db import MonitorDB
from .dedup import dedup_config, dedup_keys
from .emailer import (
    EmailSettings,
    compose_opportunity_application_email,
    send_email,
    validate_user_profile_for_email,
)
from .extraction import extract_emails
from .feed import (
    FeedItem,
    configured_wechat2rss_feed_configs,
    discover_wechat2rss_feeds,
    discover_werss_feeds,
    fetch_article_text,
    fetch_feed,
    stable_id,
)
from .feishu import FeishuClient, FeishuSettings, opportunity_card
from .opportunity import OpportunityAnalysis, analyse_opportunity
from .schedule_inbox import sync_opportunity_to_schedule_inbox
from .scoring import ScoreResult, score_opportunity, scoring_config
from .settings import load_project_cached
from .team_server import (
    configured_feeds,
    materialize_opportunity_for_users,
    push_unpushed_opportunities,
)
from .volunteer import (
    analyse_volunteer_opportunity,
    maybe_send_volunteer_reminder,
    volunteer_enabled,
    volunteer_source_allowed,
)

Reporter = Callable[[str], None]


def _silent(_: str) -> None:
    return None


@dataclass
class ScanContext:
    root: Path
    db: MonitorDB
    app_config: dict[str, Any]
    schedule_config: dict[str, Any]
    feishu_client: FeishuClient | None = None
    report: Reporter = _silent
    counts: dict[str, Any] = field(default_factory=dict)
    cycle_id: str = ""

    @property
    def monitor_config(self) -> dict[str, Any]:
        return self.app_config.get("monitor") or {}

    @property
    def timeout(self) -> int:
        return int(self.monitor_config.get("article_timeout_seconds", 15))

    @property
    def fetch_html(self) -> bool:
        return bool(self.monitor_config.get("fetch_article_html", True))

    def bump(self, key: str, value: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + value


def build_scan_context(
    root: Path,
    db: MonitorDB,
    feishu_client: FeishuClient | None = None,
    *,
    app_config: dict[str, Any] | None = None,
    schedule_config: dict[str, Any] | None = None,
    reporter: Reporter | None = None,
) -> ScanContext:
    if app_config is None or schedule_config is None:
        _paths, loaded_app, loaded_schedule = load_project_cached(root)
        app_config = app_config if app_config is not None else loaded_app
        schedule_config = schedule_config if schedule_config is not None else loaded_schedule
    cycle_id = uuid4().hex[:8]
    counts = new_counts()
    counts["cycle_id"] = cycle_id
    return ScanContext(
        root=Path(root),
        db=db,
        app_config=app_config,
        schedule_config=schedule_config,
        feishu_client=feishu_client,
        report=reporter or _silent,
        counts=counts,
        cycle_id=cycle_id,
    )


def new_counts() -> dict[str, int]:
    return {
        "feeds": 0,
        "feed_failed": 0,
        "items": 0,
        "item_failed": 0,
        "new_articles": 0,
        "duplicates_merged": 0,
        "score_disagreements": 0,
        "score_suppressed": 0,
        "opportunities": 0,
        "user_opportunities": 0,
        "sent_cards": 0,
        "push_sent": 0,
        "push_skipped": 0,
        "push_failed": 0,
        "emails_sent": 0,
        "emails_skipped": 0,
        "emails_failed": 0,
        "volunteer_reminders_sent": 0,
        "volunteer_reminders_skipped": 0,
        "volunteer_reminders_failed": 0,
        "inbox_sent": 0,
        "inbox_skipped": 0,
        "inbox_failed": 0,
        "interval": 0,
    }


def collect_feed_configs(ctx: ScanContext) -> list[dict[str, Any]]:
    """合并 feed 来源：管理端配置(db) 或 app.yml → WeRSS 发现 → Wechat2RSS 配置+发现。"""
    feed_configs = configured_feeds(ctx.db, ctx.app_config)
    known_urls = {str(item.get("url") or "") for item in feed_configs}

    def extend(new_feeds: list[dict[str, Any]]) -> None:
        for feed in new_feeds:
            url = str(feed.get("url") or "")
            if url and url not in known_urls:
                feed_configs.append(feed)
                known_urls.add(url)

    try:
        extend(discover_werss_feeds(ctx.app_config, timeout=ctx.timeout))
    except Exception as exc:  # noqa: BLE001
        ctx.db.add_log("warning", f"discover WeRSS feeds failed: {exc}", {})
    try:
        wechat2rss_feeds = configured_wechat2rss_feed_configs(ctx.app_config)
        wechat2rss_feeds.extend(discover_wechat2rss_feeds(ctx.app_config, timeout=ctx.timeout))
        extend(wechat2rss_feeds)
    except Exception as exc:  # noqa: BLE001
        ctx.db.add_log("warning", f"discover Wechat2RSS feeds failed: {exc}", {})
    return feed_configs


def scan_cycle(
    root: Path,
    db: MonitorDB,
    feishu_client: FeishuClient | None = None,
    *,
    app_config: dict[str, Any] | None = None,
    schedule_config: dict[str, Any] | None = None,
    force: bool = False,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    ctx = build_scan_context(
        root,
        db,
        feishu_client,
        app_config=app_config,
        schedule_config=schedule_config,
        reporter=reporter,
    )
    interval = int(
        os.getenv("CHECK_INTERVAL_SECONDS")
        or ctx.monitor_config.get("check_interval_seconds", 300)
    )
    ctx.counts["interval"] = interval
    ctx.report(f"扫描周期 {ctx.cycle_id} 开始")

    feed_configs = collect_feed_configs(ctx)
    ctx.counts["feeds"] = len(feed_configs)

    for feed_config in feed_configs:
        name = str(feed_config.get("name") or "未命名来源")
        url = str(feed_config.get("url") or "")
        try:
            items = fetch_feed(feed_config, timeout=ctx.timeout)
        except Exception as exc:  # noqa: BLE001
            ctx.bump("feed_failed")
            failures = ctx.db.record_feed_result(url, name, False, error=str(exc))
            ctx.db.add_log(
                "warning",
                f"fetch feed failed: {exc}",
                {"cycle_id": ctx.cycle_id, "name": name, "url": url, "consecutive_failures": failures},
            )
            if failures in {3, 10} or failures % 50 == 0:
                # 连败达到关口时升级记录一次，避免每轮刷屏
                ctx.db.add_log(
                    "error",
                    f"feed unhealthy: {failures} consecutive failures",
                    {"cycle_id": ctx.cycle_id, "name": name, "url": url, "last_error": str(exc)[:300]},
                )
            ctx.report(f"feed 读取失败，已跳过：{name} - {exc}")
            continue
        ctx.db.record_feed_result(url, name, True, items=len(items))
        ctx.report(f"读取 feed：{name}，{len(items)} 条")
        for item in items:
            ctx.bump("items")
            if ctx.db.article_exists(item.item_id) and not force:
                continue
            try:
                process_new_item(ctx, item)
            except Exception as exc:  # noqa: BLE001
                ctx.bump("item_failed")
                ctx.db.add_log(
                    "error",
                    f"process item failed: {exc}",
                    {
                        "cycle_id": ctx.cycle_id,
                        "title": item.title,
                        "link": item.link,
                        "source": item.source_name,
                    },
                )
                ctx.report(f"条目处理失败，已跳过：{item.title} - {exc}")

    push_counts = push_unpushed_opportunities(ctx.db)
    ctx.counts["push_sent"] = push_counts["sent"]
    ctx.counts["push_skipped"] = push_counts["skipped"]
    ctx.counts["push_failed"] = push_counts["failed"]

    ctx.db.add_log("info", "scan cycle completed", {"cycle_id": ctx.cycle_id, **ctx.counts})
    logging_config = ctx.app_config.get("logging") or {}
    ctx.db.prune_logs(
        keep_days=int(logging_config.get("keep_days", 14)),
        max_rows=int(logging_config.get("max_rows", 5000)),
    )
    return ctx.counts


def process_new_item(ctx: ScanContext, item: FeedItem, *, fetch_html: bool | None = None) -> None:
    """处理一条尚未见过的 feed 条目：记录文章 → 分析 → 内容去重 → 路由动作。"""
    ctx.bump("new_articles")
    ctx.db.insert_article(item.item_id, item.source_name, item.title, item.link, item.published)
    use_html = ctx.fetch_html if fetch_html is None else fetch_html
    text = article_text(item, use_html, ctx.timeout)

    analysis = analyse_opportunity(
        item.title,
        text,
        item.link,
        item.source_name,
        item.item_id,
        ctx.app_config,
        ctx.schedule_config,
    )
    volunteer_analysis: OpportunityAnalysis | None = None
    if volunteer_enabled(ctx.app_config) and volunteer_source_allowed(ctx.app_config, item.source_name):
        candidate = analyse_volunteer_opportunity(
            item.title,
            text,
            item.link,
            item.source_name,
            item.item_id,
            ctx.app_config,
            ctx.schedule_config,
        )
        if candidate.is_target:
            volunteer_analysis = candidate

    # 内容评分：shadow_mode 下只记录分数与新旧判定分歧；关闭 shadow 后按三档拦截
    score_result = maybe_score_item(ctx, item, text, analysis, volunteer_analysis)

    if not analysis.is_target and volunteer_analysis is None:
        ctx.report(f"- 跳过：{item.title}（{'；'.join(analysis.reasons) or '未命中'}）")
        return

    if score_result is not None and not bool(scoring_config(ctx.app_config).get("shadow_mode", True)):
        if score_result.verdict == "ignore":
            ctx.bump("score_suppressed")
            ctx.db.add_log(
                "info",
                "opportunity suppressed by score",
                {"title": item.title, "score": score_result.score, "reasons": score_result.reasons},
            )
            ctx.report(f"- 评分过低已忽略（{score_result.score} 分）：{item.title}")
            return

    # 内容级去重：同一文章换链接/换 guid、或被其他公众号转发，只提醒一次
    keys = content_dedup_keys(ctx, item)
    records = ctx.db.find_content_fingerprints(keys) if keys else []
    fresh_records = [record for record in records if fingerprint_within_window(ctx, record)]
    duplicate = fresh_records[0] if fresh_records else None
    if duplicate:
        ctx.bump("duplicates_merged")
        ctx.db.register_content_fingerprints(
            keys, str(duplicate["opportunity_id"]), item.item_id, item.source_name, item.title
        )
        ctx.db.add_log(
            "info",
            "duplicate content merged",
            {
                "title": item.title,
                "source": item.source_name,
                "merged_into": duplicate["opportunity_id"],
                "first_source": duplicate.get("source_name"),
            },
        )
        ctx.report(f"- 重复内容，合并到已有机会：{item.title}（首见于 {duplicate.get('source_name') or '未知来源'}）")
        return

    # 非 shadow 且分数只够进收件箱：创建机会但不发打扰式提醒（志愿确认邮件/飞书卡片）
    notify_allowed = True
    if score_result is not None and not bool(scoring_config(ctx.app_config).get("shadow_mode", True)):
        notify_allowed = score_result.verdict == "notify"

    if analysis.is_target:
        handle_opportunity(ctx, analysis, score=score_result, notify=notify_allowed)
    if volunteer_analysis is not None:
        handle_volunteer_opportunity(ctx, volunteer_analysis, score=score_result, notify=notify_allowed)

    if keys:
        primary_id = analysis.id if analysis.is_target else volunteer_analysis.id  # type: ignore[union-attr]
        # 存在过期指纹时重置为本期新机会，避免旧一期的记录长期挡住合并
        ctx.db.register_content_fingerprints(
            keys, primary_id, item.item_id, item.source_name, item.title, reset=bool(records)
        )


def content_dedup_keys(ctx: ScanContext, item: FeedItem) -> list[str]:
    config = dedup_config(ctx.app_config)
    if not bool(config.get("enabled", True)):
        return []
    return dedup_keys(
        item.title,
        item.link,
        min_title_chars=int(config.get("min_title_chars", 8)),
    )


def fingerprint_within_window(ctx: ScanContext, record: dict[str, Any]) -> bool:
    """只在去重时间窗内合并；过期指纹视为新一期活动，允许重新提醒。"""
    window_days = int(dedup_config(ctx.app_config).get("window_days", 14))
    try:
        first_seen = datetime.fromisoformat(str(record["first_seen_at"]))
    except ValueError:
        return True
    if first_seen.tzinfo is None:
        # 历史/手工数据可能是无时区时间，按 UTC 处理，避免 naive/aware 相减抛错
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - first_seen <= timedelta(days=max(1, window_days))


def process_single_item(ctx: ScanContext, item: FeedItem, *, force: bool = False, fetch_html: bool | None = None) -> dict[str, Any]:
    """供 CLI manual 命令使用：处理单条（如手动粘贴的文章链接）。"""
    if ctx.db.article_exists(item.item_id) and not force:
        ctx.report("这个链接已经处理过。如需重跑，请加 --reprocess。")
        return dict(ctx.counts)
    process_new_item(ctx, item, fetch_html=fetch_html)
    return dict(ctx.counts)


def handle_opportunity(
    ctx: ScanContext,
    analysis: OpportunityAnalysis,
    *,
    score: ScoreResult | None = None,
    notify: bool = True,
) -> None:
    ctx.bump("opportunities")
    score_label = f"，{score.score} 分" if score else ""
    ctx.report(f"- 检出机会（{analysis.category_label}{score_label}）：{analysis.title}")
    message_id = ""
    if notify:
        message_id = maybe_send_opportunity_card(ctx.db, ctx.feishu_client, analysis)
    status = "pending_decision" if message_id else "new_no_chat"
    ctx.db.upsert_opportunity(payload_with_score(analysis.to_db_payload(status=status, message_id=message_id), score))
    if message_id:
        ctx.bump("sent_cards")

    # inbox 档不做任何打扰式动作：自动投递邮件同样属于打扰，必须受 notify 拦截
    if notify:
        email_status = maybe_auto_send_opportunity_email(ctx.db, ctx.app_config, analysis)
    else:
        email_status = "skipped"
    if email_status in {"sent", "dry_run_sent"}:
        ctx.bump("emails_sent")
    elif email_status == "failed":
        ctx.bump("emails_failed")
    elif email_status == "skipped":
        ctx.bump("emails_skipped")

    ctx.bump("user_opportunities", materialize_opportunity_for_users(ctx.db, analysis))
    if not notify:
        suppress_push_for_opportunity(ctx, analysis.id)
    sync_to_inbox(ctx, analysis, status=status, message_id=message_id)


def handle_volunteer_opportunity(
    ctx: ScanContext,
    analysis: OpportunityAnalysis,
    *,
    score: ScoreResult | None = None,
    notify: bool = True,
) -> None:
    ctx.bump("opportunities")
    score_label = f"（{score.score} 分）" if score else ""
    ctx.report(f"- 检出志愿机会{score_label}：{analysis.title}")
    # pending_confirmation 意味着"确认邮件已发、等待回复"；inbox 档没发邮件，
    # 状态用 pending_decision（在收件箱里等用户决策），避免语义不一致
    status = "pending_confirmation" if notify else "pending_decision"
    ctx.db.upsert_opportunity(payload_with_score(analysis.to_db_payload(status=status), score))
    ctx.bump("user_opportunities", materialize_opportunity_for_users(ctx.db, analysis))
    if not notify:
        suppress_push_for_opportunity(ctx, analysis.id)
    sync_to_inbox(ctx, analysis, status=status, message_id="")

    if not notify:
        ctx.bump("volunteer_reminders_skipped")
        ctx.report("  分数未达提醒线，只进收件箱，不发确认邮件")
        return
    reminder_status = maybe_send_volunteer_reminder(ctx.db, ctx.app_config, analysis)
    if reminder_status == "sent":
        ctx.bump("volunteer_reminders_sent")
        ctx.report("  已发送志愿确认邮件")
    elif reminder_status == "failed":
        ctx.bump("volunteer_reminders_failed")
    elif reminder_status == "skipped":
        ctx.bump("volunteer_reminders_skipped")


def suppress_push_for_opportunity(ctx: ScanContext, opportunity_id: str) -> None:
    """inbox 档机会不做 Web 推送：预先标记为已推送，扫描末尾的推送循环会跳过。"""
    for user in ctx.db.list_users():
        ctx.db.mark_user_opportunity_pushed(str(user["id"]), opportunity_id)


def payload_with_score(payload: dict[str, Any], score: ScoreResult | None) -> dict[str, Any]:
    if score is not None:
        payload["score"] = score.score
        payload["score_reasons"] = score.reasons_text()
    return payload


def maybe_score_item(
    ctx: ScanContext,
    item: FeedItem,
    text: str,
    analysis: OpportunityAnalysis,
    volunteer_analysis: OpportunityAnalysis | None,
) -> ScoreResult | None:
    config = scoring_config(ctx.app_config)
    if not bool(config.get("enabled", True)):
        return None
    effective = volunteer_analysis or analysis
    result = score_opportunity(item.title, text, effective, ctx.app_config)

    # 影子对比：布尔判定与评分三档意见不一致时记录，供调阈值参考
    is_any_target = analysis.is_target or volunteer_analysis is not None
    disagreement = ""
    if is_any_target and result.verdict == "ignore":
        disagreement = "boolean_target_but_score_ignore"
    elif not is_any_target and result.verdict == "notify":
        disagreement = "boolean_skip_but_score_notify"
    if disagreement:
        ctx.bump("score_disagreements")
        ctx.db.add_log(
            "info",
            f"score disagreement: {disagreement}",
            {
                "title": item.title,
                "source": item.source_name,
                "score": result.score,
                "verdict": result.verdict,
                "reasons": result.reasons,
            },
        )
    return result


def sync_to_inbox(ctx: ScanContext, analysis: OpportunityAnalysis, *, status: str, message_id: str) -> None:
    inbox_result = sync_opportunity_to_schedule_inbox(
        ctx.db,
        ctx.app_config,
        ctx.db.get_opportunity(analysis.id) or analysis.to_db_payload(status=status, message_id=message_id),
    )
    inbox_status = str(inbox_result.get("status") or "failed")
    if inbox_status == "synced":
        ctx.bump("inbox_sent")
    elif inbox_status == "failed":
        ctx.bump("inbox_failed")
    else:
        ctx.bump("inbox_skipped")


def article_text(item: FeedItem, fetch_html: bool, timeout: int) -> str:
    if fetch_html and item.link:
        try:
            return fetch_article_text(item.link, timeout=timeout)
        except Exception:
            return item.summary
    return item.summary


def maybe_send_opportunity_card(
    db: MonitorDB,
    feishu_client: FeishuClient | None,
    analysis: OpportunityAnalysis,
) -> str:
    chat_id = db.get_binding("default_chat_id") or FeishuSettings.from_env().default_chat_id
    if not feishu_client or not chat_id or not FeishuSettings.from_env().enabled:
        return ""
    payload = analysis.to_db_payload(status="pending_decision")
    payload["category_label"] = analysis.category_label
    try:
        return feishu_client.send_message(chat_id, opportunity_card(payload))
    except Exception as exc:  # noqa: BLE001
        db.add_log("error", f"send feishu card failed: {exc}", {"opportunity_id": analysis.id})
        return ""


def maybe_auto_send_opportunity_email(
    db: MonitorDB,
    app_config: dict[str, Any],
    analysis: OpportunityAnalysis,
) -> str:
    from .config import get_bool_env

    email_config = app_config.get("email") or {}
    enabled = bool(email_config.get("auto_send_opportunities", False)) or get_bool_env(
        "AUTO_SEND_OPPORTUNITY_EMAIL", False
    )
    if not enabled:
        return "disabled"

    categories = [str(item) for item in (email_config.get("auto_send_categories") or []) if str(item).strip()]
    if categories and analysis.category not in categories:
        db.add_log(
            "info",
            "auto opportunity email skipped by category",
            {"opportunity_id": analysis.id, "category": analysis.category},
        )
        return "skipped"

    if analysis.schedule_status == "conflict":
        db.add_log(
            "info",
            "auto opportunity email skipped by schedule conflict",
            {"opportunity_id": analysis.id, "title": analysis.title},
        )
        return "skipped"

    profile_missing = validate_user_profile_for_email(app_config)
    if profile_missing:
        db.add_log(
            "error",
            "auto opportunity email failed: missing user profile",
            {"opportunity_id": analysis.id, "missing": profile_missing},
        )
        return "failed"

    emails = extract_emails(analysis.raw_text)
    if len(emails) != 1:
        db.add_log(
            "info",
            "auto opportunity email skipped by recipient count",
            {"opportunity_id": analysis.id, "email_count": len(emails), "emails": emails},
        )
        return "skipped"

    recipient = emails[0]
    if db.email_send_exists(analysis.id, recipient):
        return "skipped"

    settings = EmailSettings.from_env()
    missing = settings.validate(require_password=not settings.dry_run)
    if missing:
        db.add_log(
            "error",
            "auto opportunity email failed: missing email settings",
            {"opportunity_id": analysis.id, "missing": missing},
        )
        return "failed"

    send_id = stable_id("email", analysis.id, recipient)
    try:
        msg = compose_opportunity_application_email(app_config, analysis, recipient)
        send_email(settings, msg, recipient)
    except Exception as exc:  # noqa: BLE001
        db.record_email_send(
            send_id,
            analysis.id,
            analysis.article_item_id,
            recipient,
            analysis.title,
            "failed",
            str(exc),
        )
        db.add_log(
            "error",
            f"auto opportunity email failed: {exc}",
            {"opportunity_id": analysis.id, "recipient": recipient},
        )
        return "failed"

    status = "dry_run_sent" if settings.dry_run else "sent"
    db.record_email_send(
        send_id,
        analysis.id,
        analysis.article_item_id,
        recipient,
        str(msg["Subject"] or analysis.title),
        status,
    )
    db.add_log(
        "info",
        "auto opportunity email processed",
        {"opportunity_id": analysis.id, "recipient": recipient, "status": status},
    )
    return status
