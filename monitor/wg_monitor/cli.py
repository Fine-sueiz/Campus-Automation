from __future__ import annotations

import argparse
from pathlib import Path

from .config import ConfigError, load_project
from .db import MonitorDB, default_db_path
from .feed import FeedItem, fetch_article_text, stable_id
from .local_feishu import LocalFeishuOptions, run_local_feishu
from .monitor import run_loop, run_once, validate_project
from .pipeline import build_scan_context, process_single_item
from .schedule import compute_free_blocks, format_free_blocks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m wg_monitor",
        description="公众号勤工助学岗位监测与自动投递工具",
    )
    parser.add_argument("--root", default=".", help="项目根目录，默认当前目录")

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="检查配置、课表、邮箱和 feed")
    validate.add_argument("--skip-network", action="store_true", help="不检查 feed 网络可用性")

    monitor = subparsers.add_parser("monitor", help="运行 feed 监测")
    mode = monitor.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="只运行一次")
    mode.add_argument("--loop", action="store_true", help="循环运行")
    monitor.add_argument("--reprocess", action="store_true", help="重新处理已见过的文章")

    manual = subparsers.add_parser("manual", help="手动粘贴公众号文章链接处理")
    manual.add_argument("--url", required=True, help="公众号文章链接或本地 HTML 文件路径")
    manual.add_argument("--title", default="", help="可选，手动指定文章标题")
    manual.add_argument("--reprocess", action="store_true", help="重新处理同一个链接")

    subparsers.add_parser("free-time", help="输出当前课表空闲时间")

    local_feishu = subparsers.add_parser("local-feishu", help="本地飞书长连接 MVP：手机卡片确认，无需服务器")
    local_feishu.add_argument("--once", action="store_true", help="只扫描一次并退出，不启动长连接")
    local_feishu.add_argument("--bind-only", action="store_true", help="只监听飞书绑定/按钮，不启动扫描循环")
    local_feishu.add_argument("--test-card", action="store_true", help="向已绑定群聊发送一张测试卡片")
    local_feishu.add_argument("--interval", type=int, default=0, help="覆盖扫描间隔秒数，默认使用 config/app.yml")
    local_feishu.add_argument("--no-run-tasks", action="store_true", help="点参加后只创建报名任务，不立即调用问卷助手")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        paths, app_config, schedule_config = load_project(Path(args.root))
        if args.command == "validate":
            return validate_project(paths, app_config, schedule_config, check_network=not args.skip_network)
        if args.command == "monitor":
            if args.once:
                return run_once(paths, app_config, schedule_config, reprocess=args.reprocess)
            return run_loop(paths, app_config, schedule_config)
        if args.command == "manual":
            return run_manual(paths, app_config, schedule_config, args.url, args.title, args.reprocess)
        if args.command == "free-time":
            print(format_free_blocks(compute_free_blocks(schedule_config)))
            return 0
        if args.command == "local-feishu":
            return run_local_feishu(
                paths.root,
                LocalFeishuOptions(
                    once=args.once,
                    bind_only=args.bind_only,
                    test_card=args.test_card,
                    interval=args.interval,
                    run_tasks_after_join=not args.no_run_tasks,
                ),
            )
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    parser.print_help()
    return 1


def run_manual(
    paths,
    app_config,
    schedule_config,
    url: str,
    title: str,
    reprocess: bool,
) -> int:
    timeout = int((app_config.get("monitor") or {}).get("article_timeout_seconds", 15))
    text = fetch_article_text(url, timeout=timeout)
    first_line = text.splitlines()[0][:80] if text.strip() else ""
    item = FeedItem(
        source_name="手动链接",
        item_id=stable_id(url),
        title=title or first_line or "手动公众号文章",
        link=url,
        summary=text,
    )
    db = MonitorDB(default_db_path(paths.root))
    ctx = build_scan_context(
        paths.root,
        db,
        app_config=app_config,
        schedule_config=schedule_config,
        reporter=print,
    )
    process_single_item(ctx, item, force=reprocess, fetch_html=False)
    counts = ctx.counts
    print(
        f"处理完成：检出机会 {counts.get('opportunities', 0)}，"
        f"志愿提醒 {counts.get('volunteer_reminders_sent', 0)}。"
    )
    return 0
