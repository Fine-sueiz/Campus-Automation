from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .qq_sync import load_config, normalize_name, normalize_text
from .settings import PROJECT_ROOT, SHANGHAI_TZ, get_api_key, get_schedule_api_base


STATE_PATH = PROJECT_ROOT / "data" / "qq_watcher_state.json"
STATUS_PATH = PROJECT_ROOT / "data" / "qq_watcher_status.json"
COMMON_UI_LINES = {
    "发送",
    "关闭",
    "最小化",
    "最大化",
    "群公告",
    "群文件",
    "聊天记录",
    "表情",
    "截图",
    "图片",
    "文件",
    "语音",
    "视频",
}


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen": [], "created_at": now_iso()}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seen": [], "created_at": now_iso()}
    data.setdefault("seen", [])
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen = list(dict.fromkeys(state.get("seen", [])))[-3000:]
    state["seen"] = seen
    state["updated_at"] = now_iso()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def write_status(status: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    status["updated_at"] = now_iso()
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def is_noise_line(line: str) -> bool:
    line = normalize_text(line)
    if not line:
        return True
    if line in COMMON_UI_LINES:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", line):
        return True
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", line):
        return True
    return False


def message_key(group_name: str, sender_name: str, text: str) -> str:
    raw = "|".join([group_name, sender_name, normalize_text(text)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def teacher_names(group: dict[str, Any]) -> list[str]:
    names = [str(item).strip() for item in group.get("teacher_names") or []]
    return [name for name in names if name and "示例" not in name]


def line_is_teacher(line: str, names: list[str]) -> str | None:
    normalized_line = normalize_name(line)
    for name in names:
        normalized_name = normalize_name(name)
        if not normalized_name:
            continue
        if normalized_line == normalized_name:
            return name
        if normalized_line.startswith(normalized_name):
            rest = line[len(name) :].strip(" ：:,-，")
            if rest:
                return name
    return None


def text_after_teacher_prefix(line: str, name: str) -> str:
    if normalize_name(line) == normalize_name(name):
        return ""
    if normalize_name(line).startswith(normalize_name(name)):
        return line[len(name) :].strip(" ：:,-，")
    return ""


def collect_following_text(lines: list[str], start_index: int, names: list[str]) -> str:
    parts: list[str] = []
    for line in lines[start_index + 1 : start_index + 8]:
        if line_is_teacher(line, names):
            break
        if is_noise_line(line):
            continue
        parts.append(line)
        if len(" ".join(parts)) >= 600:
            break
    return "\n".join(parts).strip()


def extract_teacher_messages(lines: list[str], group: dict[str, Any], window_title: str) -> list[dict[str, str]]:
    names = teacher_names(group)
    if not names:
        return []

    results: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        sender_name = line_is_teacher(line, names)
        if not sender_name:
            continue
        text = text_after_teacher_prefix(line, sender_name)
        if not text:
            text = collect_following_text(lines, index, names)
        text = normalize_text(text)
        if not text or is_noise_line(text):
            continue
        key = message_key(window_title, sender_name, text)
        results.append(
            {
                "external_key": key,
                "group_name": str(group.get("group_name") or window_title),
                "sender_name": sender_name,
                "course_name": str(group.get("course_name") or group.get("group_name") or window_title),
                "text": text,
            }
        )
    return results


def window_title_matches(title: str, group: dict[str, Any]) -> bool:
    configured = str(group.get("group_name") or "").strip()
    if not configured:
        return False
    return normalize_name(configured) in normalize_name(title)


def read_window_lines(window) -> list[str]:
    raw_lines: list[str] = []
    try:
        elements = window.descendants()
    except Exception:  # noqa: BLE001
        elements = []
    for element in elements:
        try:
            text = element.window_text()
        except Exception:  # noqa: BLE001
            continue
        for line in str(text).splitlines():
            line = normalize_text(line)
            if line and not is_noise_line(line):
                raw_lines.append(line)
    return list(dict.fromkeys(raw_lines))


def discover_messages() -> tuple[list[dict[str, str]], dict[str, Any]]:
    config = load_config()
    groups = [group for group in config.get("groups", []) if isinstance(group, dict)]
    if not config.get("enabled", True):
        return [], {"enabled": False, "windows_found": 0, "groups": len(groups)}

    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("缺少 pywinauto，请重新运行 .\\start_qq_watcher.ps1 安装依赖") from exc

    desktop = Desktop(backend="uia")
    windows = desktop.windows(visible_only=True)
    messages: list[dict[str, str]] = []
    matched_titles: list[str] = []
    for window in windows:
        try:
            title = normalize_text(window.window_text())
        except Exception:  # noqa: BLE001
            continue
        if not title:
            continue
        for group in groups:
            if not window_title_matches(title, group):
                continue
            matched_titles.append(title)
            lines = read_window_lines(window)
            messages.extend(extract_teacher_messages(lines, group, title))
            break

    return messages, {
        "enabled": True,
        "windows_found": len(matched_titles),
        "window_titles": matched_titles,
        "groups": len(groups),
    }


def post_message(api_base: str, api_key: str, message: dict[str, str]) -> dict[str, Any]:
    payload = {
        **message,
        "message_time": now_iso(),
        "raw": {"collector": "qq_watcher", "captured_at": now_iso()},
    }
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/integrations/qq/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"日程后端拒绝消息：{exc.code} {body}") from exc
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"发送 QQ 消息到日程后端失败：{exc}") from exc


def run(api_base: str, api_key: str, interval: float, import_visible: bool) -> None:
    state = load_state()
    seen = set(state.get("seen", []))
    primed = bool(seen) or import_visible
    print(f"[{now_iso()}] QQ 监听器启动，后端：{api_base}", flush=True)
    print(f"[{now_iso()}] 状态文件：{STATUS_PATH}", flush=True)

    while True:
        loop_status: dict[str, Any] = {
            "running": True,
            "api_base": api_base,
            "last_scan_at": now_iso(),
            "posted": 0,
            "errors": [],
        }
        try:
            messages, scan_status = discover_messages()
            loop_status.update(scan_status)
            new_messages = [message for message in messages if message["external_key"] not in seen]

            if not primed:
                for message in new_messages:
                    seen.add(message["external_key"])
                primed = True
                loop_status["primed"] = len(new_messages)
                print(f"[{now_iso()}] 已把当前可见消息作为起点：{len(new_messages)} 条", flush=True)
            else:
                for message in new_messages:
                    result = post_message(api_base, api_key, message)
                    seen.add(message["external_key"])
                    loop_status["posted"] += 1
                    print(
                        f"[{now_iso()}] 已发送：{message['group_name']} / {message['sender_name']} -> {result.get('status')}",
                        flush=True,
                    )

            state["seen"] = list(seen)
            save_state(state)
        except Exception as exc:  # noqa: BLE001
            loop_status["errors"].append(str(exc))
            print(f"[{now_iso()}] {exc}", file=sys.stderr, flush=True)

        write_status(loop_status)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="监听已打开的 QQ 群窗口，并把老师消息写入日程后端。")
    parser.add_argument("--api-base", default=get_schedule_api_base())
    parser.add_argument("--api-key", default=get_api_key())
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--import-visible",
        action="store_true",
        help="启动时立即导入当前窗口里可见的老师消息；默认只监听启动后的新消息。",
    )
    args = parser.parse_args()
    run(args.api_base, args.api_key, max(1.5, args.interval), args.import_visible)


if __name__ == "__main__":
    main()
