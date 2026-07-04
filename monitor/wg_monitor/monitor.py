"""CLI/GUI 的扫描入口。

自流水线统一后，run_once/run_loop 只是 pipeline.scan_cycle 的薄封装：
与 8011 服务端共用同一条 拉取→去重→分析→动作 流水线，
去重状态统一记在 SQLite（data/campus_monitor.sqlite3），
旧的 data/state.json 不再读写。
"""

from __future__ import annotations

import time
from typing import Any

from .config import ConfigError, ProjectPaths
from .db import MonitorDB, default_db_path
from .emailer import EmailSettings, validate_user_profile_for_email
from .feed import (
    configured_wechat2rss_feed_configs,
    discover_wechat2rss_feeds,
    discover_werss_feeds,
    fetch_feed,
)
from .pipeline import scan_cycle
from .schedule import compute_free_blocks, format_free_blocks


def validate_project(
    paths: ProjectPaths,
    app_config: dict[str, Any],
    schedule_config: dict[str, Any],
    check_network: bool = True,
) -> int:
    problems: list[str] = []
    print(f"项目目录：{paths.root}")

    email_settings = EmailSettings.from_env()
    email_missing = email_settings.validate(require_password=not email_settings.dry_run)
    if email_missing:
        problems.append(f"邮件配置缺失：{', '.join(email_missing)}")
    else:
        mode = "dry-run，不会真实发送" if email_settings.dry_run else "真实发送"
        print(f"邮件配置：OK（{mode}）")

    profile_missing = validate_user_profile_for_email(app_config)
    if profile_missing:
        problems.append(f"自动邮件个人信息未填写完整：{', '.join(profile_missing)}")

    try:
        free_by_day = compute_free_blocks(schedule_config)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"课表配置错误：{exc}")
    else:
        print("课表配置：OK")
        print(format_free_blocks(free_by_day))

    feed_urls = monitor_feed_configs(app_config, timeout=int((app_config.get("monitor") or {}).get("article_timeout_seconds", 15)))
    if not feed_urls:
        problems.append("monitor.feed_urls 为空")
    elif check_network:
        timeout = int((app_config.get("monitor") or {}).get("article_timeout_seconds", 15))
        for feed_config in feed_urls:
            try:
                items = fetch_feed(feed_config, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"feed 不可用：{feed_config.get('name')} - {exc}")
            else:
                print(f"feed：{feed_config.get('name')} OK，读取到 {len(items)} 条")

    if problems:
        print("\n发现问题：")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("\n全部检查通过。")
    return 0


def monitor_feed_configs(app_config: dict[str, Any], timeout: int = 15) -> list[dict[str, Any]]:
    """validate 命令用的 feed 汇总（yml + 自动发现，不含管理端 db 配置）。"""
    monitor_config = app_config.get("monitor") or {}
    feed_urls = list(monitor_config.get("feed_urls") or [])
    known_urls = {str(item.get("url") or "") for item in feed_urls if isinstance(item, dict)}
    for discover in (
        lambda: discover_werss_feeds(app_config, timeout=timeout),
        lambda: configured_wechat2rss_feed_configs(app_config),
        lambda: discover_wechat2rss_feeds(app_config, timeout=timeout),
    ):
        try:
            for feed in discover():
                url = str(feed.get("url") or "")
                if url and url not in known_urls:
                    feed_urls.append(feed)
                    known_urls.add(url)
        except Exception as exc:  # noqa: BLE001
            print(f"自动发现 feed 失败：{exc}")
    return feed_urls


def run_once(
    paths: ProjectPaths,
    app_config: dict[str, Any],
    schedule_config: dict[str, Any],
    reprocess: bool = False,
) -> int:
    db = MonitorDB(default_db_path(paths.root))
    counts = scan_cycle(
        paths.root,
        db,
        None,
        app_config=app_config,
        schedule_config=schedule_config,
        force=reprocess,
        reporter=print,
    )
    if counts["feeds"] == 0:
        raise ConfigError("monitor.feed_urls 为空，请先在 config/app.yml 中填写 feed 地址")
    print(
        f"\n本次扫描完成：feed {counts['feeds']} 个（失败 {counts['feed_failed']}），"
        f"条目 {counts['items']}，新文章 {counts['new_articles']}，"
        f"检出机会 {counts['opportunities']}，志愿提醒 {counts['volunteer_reminders_sent']}。"
    )
    return 0


def run_loop(paths: ProjectPaths, app_config: dict[str, Any], schedule_config: dict[str, Any]) -> int:
    interval = int((app_config.get("monitor") or {}).get("check_interval_seconds", 600))
    print(f"开始循环监测，每 {interval} 秒检查一次。按 Ctrl+C 停止。")
    while True:
        try:
            run_once(paths, app_config, schedule_config)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"本轮监测失败：{exc}")
        time.sleep(interval)
