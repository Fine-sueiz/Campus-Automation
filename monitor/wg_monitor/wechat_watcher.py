from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

from .config import load_project
from .feed import stable_id
from .volunteer import volunteer_source_accounts
from .wechat_integration import WechatWatcherSettings


TIME_RE = re.compile(
    r"^(?:刚刚|\d+分钟前|\d+小时前|昨天|前天|星期[一二三四五六日天]|"
    r"\d{1,2}[:：]\d{2}|\d{1,2}月\d{1,2}日|20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)$"
)
NOISE_TEXT = {
    "公众号",
    "常看的号",
    "搜索",
    "广告",
    "微信团队",
    "微信问一问",
    "订阅号消息",
    "更多",
    "图片",
}


@dataclass(frozen=True)
class UiTextNode:
    text: str
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    control_type: str = ""


@dataclass(frozen=True)
class VisibleArticle:
    external_key: str
    source_name: str
    title: str
    published_text: str = ""
    article_url: str = ""
    raw_text: str = ""


def normalized(text: str) -> str:
    return "".join(str(text or "").split()).casefold()


def clean_visible_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    previous = ""
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", cleaned)
    return cleaned.strip(" .·•。")


def is_time_text(text: str) -> bool:
    return bool(TIME_RE.fullmatch("".join(str(text or "").split())))


def article_key(source_name: str, title: str, article_url: str = "") -> str:
    return stable_id("wechat-visible", normalized(source_name), normalized(title), article_url.strip())


def is_noise(text: str, account_keys: set[str]) -> bool:
    cleaned = " ".join(text.split()).strip()
    if not cleaned or len(cleaned) < 2:
        return True
    if cleaned in NOISE_TEXT or normalized(cleaned) in account_keys:
        return True
    if is_time_text(cleaned) or cleaned.endswith("广告"):
        return True
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return True
    return False


def match_account(text: str, accounts_by_key: dict[str, str]) -> str:
    candidate = normalized(text).strip("0o.-·•()（）[]【】")
    exact = accounts_by_key.get(candidate)
    if exact:
        return exact
    if len(candidate) < 4:
        return ""
    best_source = ""
    best_score = 0.0
    for account_key, source in accounts_by_key.items():
        score = SequenceMatcher(None, candidate, account_key).ratio()
        if score > best_score:
            best_score = score
            best_source = source
    return best_source if best_score >= 0.64 else ""


def extract_from_lines(lines: list[str], accounts: list[str]) -> list[VisibleArticle]:
    accounts_by_key = {normalized(account): account for account in accounts}
    account_keys = set(accounts_by_key)
    cleaned_lines = [clean_visible_text(part) for line in lines for part in str(line).splitlines()]
    articles: list[VisibleArticle] = []
    for index, line in enumerate(cleaned_lines):
        source = match_account(line, accounts_by_key)
        if not source:
            continue
        published = ""
        title = ""
        for candidate in cleaned_lines[index + 1 : index + 7]:
            if match_account(candidate, accounts_by_key):
                break
            if is_time_text(candidate):
                published = candidate
                continue
            if not is_noise(candidate, account_keys):
                title = candidate
                break
        if title:
            articles.append(
                VisibleArticle(
                    external_key=article_key(source, title),
                    source_name=source,
                    title=title,
                    published_text=published,
                    raw_text=title,
                )
            )
    return deduplicate_articles(articles)


def extract_visible_articles(nodes: list[UiTextNode], accounts: list[str]) -> list[VisibleArticle]:
    accounts_by_key = {normalized(account): account for account in accounts}
    account_keys = set(accounts_by_key)
    ordered = sorted(nodes, key=lambda node: (node.top, node.left, node.bottom, node.right))
    articles: list[VisibleArticle] = []

    for source_node in ordered:
        source = match_account(source_node.text, accounts_by_key)
        if not source:
            continue
        candidates = [
            node
            for node in ordered
            if node is not source_node
            and source_node.top - 4 <= node.top <= source_node.bottom + 105
            and node.left < max(source_node.right + 430, 900)
        ]
        candidates.sort(key=lambda node: (node.top, node.left))
        title = ""
        published = ""
        for candidate in candidates:
            text = clean_visible_text(candidate.text)
            if not text or text == source_node.text:
                continue
            if is_time_text(text):
                published = text
                continue
            if candidate.top < source_node.bottom - 2:
                continue
            if not is_noise(text, account_keys):
                title = text
                break
        if title:
            articles.append(
                VisibleArticle(
                    external_key=article_key(source, title),
                    source_name=source,
                    title=title,
                    published_text=published,
                    raw_text=title,
                )
            )

    if not articles:
        articles = extract_from_lines([node.text for node in ordered], accounts)
    return deduplicate_articles(articles)


def deduplicate_articles(items: list[VisibleArticle]) -> list[VisibleArticle]:
    unique: dict[str, VisibleArticle] = {}
    for item in items:
        unique[item.external_key] = item
    return list(unique.values())


def read_wechat_ui(window_title: str) -> tuple[list[UiTextNode], bool]:
    try:
        from pywinauto import Desktop  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 pywinauto，请运行 python -m pip install -r requirements.txt") from exc

    desktop = Desktop(backend="uia")
    windows = [
        window
        for window in desktop.windows(visible_only=False)
        if window.window_text().strip() == window_title
    ]
    if not windows:
        raise RuntimeError(f"没有找到标题为“{window_title}”的微信窗口")
    window = max(windows, key=lambda item: item.rectangle().width() * item.rectangle().height())
    if window.is_minimized():
        raise RuntimeError("微信窗口已最小化，请恢复窗口并停留在“公众号”页面")

    nodes: list[UiTextNode] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for control in window.descendants():
        try:
            text = str(control.window_text() or "").strip()
            rect = control.rectangle()
            control_type = str(getattr(control.element_info, "control_type", "") or "")
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        for part in text.splitlines():
            cleaned = clean_visible_text(part)
            key = (cleaned, rect.left, rect.top, rect.right, rect.bottom)
            if cleaned and key not in seen:
                seen.add(key)
                nodes.append(
                    UiTextNode(
                        text=cleaned,
                        left=rect.left,
                        top=rect.top,
                        right=rect.right,
                        bottom=rect.bottom,
                        control_type=control_type,
                    )
                )
    page_detected = any(node.text in {"公众号", "常看的号", "订阅号消息"} for node in nodes)
    if not page_detected or len(nodes) < 5:
        try:
            image = window.capture_as_image()
            ocr_nodes = asyncio.run(ocr_image_nodes(image))
            if ocr_nodes:
                nodes = ocr_nodes
                page_detected = any(
                    normalized(node.text) in {normalized("公众号"), normalized("常看的号"), normalized("订阅号消息")}
                    for node in nodes
                )
        except Exception as exc:  # noqa: BLE001
            if not nodes:
                raise RuntimeError(f"微信窗口文字不可读，屏幕识别也失败：{exc}") from exc
    return nodes, page_detected


async def ocr_image_nodes(image: Any) -> list[UiTextNode]:
    try:
        from PIL import Image, ImageFilter  # type: ignore
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 Windows OCR 依赖，请重新运行 python -m pip install -r requirements.txt") from exc

    rgb_image = image.convert("RGB")
    scale = 2 if max(rgb_image.size) * 2 <= 2400 else 1
    if scale > 1:
        rgb_image = rgb_image.resize(
            (rgb_image.width * scale, rgb_image.height * scale),
            resample=Image.Resampling.LANCZOS,
        ).filter(ImageFilter.SHARPEN)

    buffer = io.BytesIO()
    rgb_image.save(buffer, format="PNG")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(buffer.getvalue())
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows 没有可用的中文 OCR 语言包")
    result = await engine.recognize_async(bitmap)

    nodes: list[UiTextNode] = []
    for line in result.lines:
        text = clean_visible_text(line.text)
        words = list(line.words)
        if not text or not words:
            continue
        left = min(int(word.bounding_rect.x) for word in words)
        top = min(int(word.bounding_rect.y) for word in words)
        right = max(int(word.bounding_rect.x + word.bounding_rect.width) for word in words)
        bottom = max(int(word.bounding_rect.y + word.bounding_rect.height) for word in words)
        nodes.append(UiTextNode(text, left, top, right, bottom, "OCR"))
    return nodes


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"initialized": False, "seen": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"initialized": False, "seen": []}
    return value if isinstance(value, dict) else {"initialized": False, "seen": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def post_articles(
    settings: WechatWatcherSettings,
    watcher_status: dict[str, Any],
    items: list[VisibleArticle],
) -> dict[str, Any]:
    response = requests.post(
        f"{settings.api_base}/api/integrations/wechat/articles",
        headers={"X-Integration-Key": settings.integration_key},
        json={"watcher": watcher_status, "items": [asdict(item) for item in items]},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"监测接口 HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def run_cycle(
    root: Path,
    settings: WechatWatcherSettings,
    state_path: Path,
    *,
    include_existing: bool = False,
) -> dict[str, Any]:
    _paths, app_config, _schedule_config = load_project(root)
    accounts = volunteer_source_accounts(app_config)
    nodes, page_detected = read_wechat_ui(settings.window_title)
    articles = extract_visible_articles(nodes, accounts)
    state = load_state(state_path)
    seen = set(str(item) for item in state.get("seen") or [])
    is_first_run = not bool(state.get("initialized"))

    if is_first_run and settings.baseline_on_first_run and not include_existing and articles:
        seen.update(article.external_key for article in articles)
        state = {"initialized": True, "seen": list(seen)[-settings.max_seen_items :]}
        save_state(state_path, state)
        result = post_articles(
            settings,
            {
                "status": "baseline_ready",
                "message": "已记录当前可见文章，之后只处理新文章",
                "visible_items": len(articles),
                "page_detected": page_detected,
                "pid": os.getpid(),
            },
            [],
        )
        return {"status": "baseline_ready", "visible": len(articles), "sent": 0, "server": result}

    if is_first_run and settings.baseline_on_first_run and not include_existing and not articles:
        result = post_articles(
            settings,
            {
                "status": "waiting_page",
                "message": "尚未识别到白名单公众号文章，请保持公众号消息页面打开",
                "visible_items": 0,
                "page_detected": page_detected,
                "pid": os.getpid(),
            },
            [],
        )
        return {"status": "waiting_page", "visible": 0, "sent": 0, "server": result}

    new_items = [article for article in articles if article.external_key not in seen]
    status = "running" if page_detected or articles else "waiting_page"
    message = "" if status == "running" else "请在微信中打开公众号消息页面"
    result = post_articles(
        settings,
        {
            "status": status,
            "message": message,
            "visible_items": len(articles),
            "page_detected": page_detected,
            "pid": os.getpid(),
        },
        new_items,
    )
    seen.update(article.external_key for article in new_items)
    state = {"initialized": True, "seen": list(seen)[-settings.max_seen_items :]}
    save_state(state_path, state)
    return {"status": status, "visible": len(articles), "sent": len(new_items), "server": result}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="微信公众号消息只读监听器")
    parser.add_argument("--root", default=".", help="监测程序根目录")
    parser.add_argument("--once", action="store_true", help="只读取一次")
    parser.add_argument("--include-existing", action="store_true", help="首次运行时也上报当前可见文章")
    parser.add_argument("--dump", action="store_true", help="只打印当前识别结果，不上报")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    _paths, app_config, _schedule_config = load_project(root)
    settings = WechatWatcherSettings.from_config(app_config)
    state_path = root / "data" / "wechat_watcher_state.json"
    if not settings.enabled:
        print("微信监听器已在配置中关闭。", flush=True)
        return 2

    if args.dump:
        nodes, page_detected = read_wechat_ui(settings.window_title)
        articles = extract_visible_articles(nodes, volunteer_source_accounts(app_config))
        print(json.dumps({"page_detected": page_detected, "items": [asdict(item) for item in articles]}, ensure_ascii=False, indent=2))
        return 0

    while True:
        try:
            result = run_cycle(
                root,
                settings,
                state_path,
                include_existing=bool(args.include_existing),
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"微信监听失败：{exc}", file=sys.stderr, flush=True)
            try:
                post_articles(
                    settings,
                    {
                        "status": "error",
                        "message": str(exc),
                        "visible_items": 0,
                        "page_detected": False,
                        "pid": os.getpid(),
                    },
                    [],
                )
            except Exception:  # noqa: BLE001
                pass
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
