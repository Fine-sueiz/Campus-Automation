from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests

from .extraction import html_to_text, normalize_text


@dataclass(frozen=True)
class FeedItem:
    source_name: str
    item_id: str
    title: str
    link: str
    published: str = ""
    summary: str = ""


class FeedError(RuntimeError):
    pass


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

DATE_RE = re.compile(r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2})")


def stable_id(*parts: str) -> str:
    raw = "\n".join(part or "" for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def read_url_or_file(url: str, timeout: int = 15) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(parsed.path).read_text(encoding="utf-8")
    if parsed.scheme in {"http", "https"}:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, text/xml, text/html, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text
    path = Path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FeedError(f"不支持或无法读取地址：{url}")


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def first_child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(node):
        if strip_namespace(child.tag) in wanted:
            return normalize_text("".join(child.itertext()))
    return ""


def first_link(node: ET.Element) -> str:
    for child in list(node):
        if strip_namespace(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        text = normalize_text("".join(child.itertext()))
        if text:
            return text
    return ""


def parse_feed_xml(xml_text: str, source_name: str = "") -> list[FeedItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedError("feed XML 解析失败，请确认 feed 地址返回 RSS/Atom") from exc

    root_name = strip_namespace(root.tag)
    items: list[FeedItem] = []

    if root_name == "rss":
        channel = next((child for child in list(root) if strip_namespace(child.tag) == "channel"), root)
        entries = [child for child in list(channel) if strip_namespace(child.tag) == "item"]
        for entry in entries:
            title = first_child_text(entry, "title")
            link = first_child_text(entry, "link") or first_link(entry)
            guid = first_child_text(entry, "guid")
            summary = first_child_text(entry, "description", "summary")
            published = first_child_text(entry, "pubDate", "published", "updated")
            item_id = guid or stable_id(title, link, published)
            items.append(FeedItem(source_name, item_id, title, link, published, html_to_text(summary)))
        return items

    entries = [child for child in list(root) if strip_namespace(child.tag) == "entry"]
    for entry in entries:
        title = first_child_text(entry, "title")
        link = first_link(entry)
        entry_id = first_child_text(entry, "id")
        summary = first_child_text(entry, "summary", "content")
        published = first_child_text(entry, "published", "updated")
        item_id = entry_id or stable_id(title, link, published)
        items.append(FeedItem(source_name, item_id, title, link, published, html_to_text(summary)))
    return items


def parse_html_list(html_text: str, source_name: str, base_url: str, feed_config: dict[str, Any] | None = None) -> list[FeedItem]:
    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError as exc:
        raise FeedError("缺少 beautifulsoup4。请先运行：python -m pip install -r requirements.txt") from exc

    config = feed_config or {}
    item_selector = str(config.get("item_selector") or "li").strip()
    link_selector = str(config.get("link_selector") or "a[href]").strip()
    date_selector = str(config.get("date_selector") or "").strip()
    limit = int(config.get("limit") or 50)

    soup = BeautifulSoup(html_text, "html.parser")
    items: list[FeedItem] = []
    seen: set[str] = set()

    for node in soup.select(item_selector):
        link_node = node.select_one(link_selector)
        if not link_node:
            continue
        title = normalize_text(link_node.get_text(" ", strip=True))
        href = str(link_node.get("href") or "").strip()
        if not title or not href or href.startswith("#"):
            continue
        link = urljoin(base_url, href)
        if link in seen:
            continue
        seen.add(link)

        date_text = ""
        if date_selector:
            date_node = node.select_one(date_selector)
            if date_node:
                date_text = normalize_text(date_node.get_text(" ", strip=True))
        if not date_text:
            all_text = normalize_text(node.get_text(" ", strip=True))
            match = DATE_RE.search(all_text)
            date_text = match.group(1).replace("年", "-").replace("月", "-").replace("/", "-").replace(".", "-") if match else ""

        item_id = stable_id(link, title, date_text)
        items.append(FeedItem(source_name, item_id, title, link, date_text, summary=title))
        if len(items) >= limit:
            break

    if not items:
        raise FeedError(f"{source_name} HTML 列表没有解析到文章，请检查 item_selector/link_selector")
    return items


def fetch_feed(feed_config: dict[str, Any], timeout: int = 15) -> list[FeedItem]:
    source_name = str(feed_config.get("name") or "未命名来源")
    url = str(feed_config.get("url") or "").strip()
    if not url:
        raise FeedError(f"{source_name} 缺少 feed url")
    raw_text = read_url_or_file(url, timeout=timeout)
    feed_type = str(feed_config.get("type") or "rss").strip().lower()
    if feed_type in {"html", "html_list", "web"}:
        return parse_html_list(raw_text, source_name=source_name, base_url=url, feed_config=feed_config)
    return parse_feed_xml(raw_text, source_name=source_name)


def fetch_article_text(url: str, timeout: int = 15) -> str:
    html = read_url_or_file(url, timeout=timeout)
    return html_to_text(html)


def parse_werss_feed_configs(xml_text: str, base_url: str) -> list[dict[str, str]]:
    base = base_url.rstrip("/")
    configs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parse_feed_xml(xml_text, source_name="WeRSS"):
        feed_id = urlparse(item.link).path.rstrip("/").rsplit("/", 1)[-1]
        if not feed_id or feed_id in seen:
            continue
        seen.add(feed_id)
        configs.append({"name": item.title or feed_id, "url": f"{base}/feed/{feed_id}.rss"})
    return configs


def discover_werss_feeds(app_config: dict[str, Any], timeout: int = 15) -> list[dict[str, str]]:
    monitor_config = app_config.get("monitor") or {}
    werss_config = monitor_config.get("werss") or {}
    if not bool(werss_config.get("enabled", False)):
        return []
    base_url = str(werss_config.get("base_url") or "http://127.0.0.1:8001").rstrip("/")
    xml_text = read_url_or_file(f"{base_url}/rss", timeout=timeout)
    return parse_werss_feed_configs(xml_text, base_url)


def wechat2rss_settings(app_config: dict[str, Any]) -> dict[str, Any]:
    monitor_config = app_config.get("monitor") or {}
    config = dict(monitor_config.get("wechat2rss") or {})
    config.setdefault("enabled", False)
    config.setdefault("base_url", "http://127.0.0.1:8001")
    config.setdefault("token", "")
    config.setdefault("discover_feeds", True)
    return config


def wechat2rss_url(app_config: dict[str, Any], path: str, **query: str) -> str:
    settings = wechat2rss_settings(app_config)
    base_url = str(settings.get("base_url") or "http://127.0.0.1:8001").rstrip("/")
    token = str(settings.get("token") or "").strip()
    params = dict(query)
    if token and "k" not in params:
        params["k"] = token
    suffix = "&".join(f"{quote(str(key))}={quote(str(value), safe='')}" for key, value in params.items() if value != "")
    path = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{path}" + (f"?{suffix}" if suffix else "")


def _list_from_wechat2rss_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "list", "feeds", "accounts", "subscriptions"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("items", "list", "feeds", "accounts", "subscriptions"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _first_text_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_wechat2rss_list_configs(json_text: str, base_url: str, token: str = "") -> list[dict[str, str]]:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise FeedError("Wechat2RSS /list 返回的不是 JSON，请确认服务地址和 token") from exc

    base = base_url.rstrip("/")
    configs: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in _list_from_wechat2rss_payload(payload):
        if not isinstance(raw, dict):
            continue
        feed_id = _first_text_value(raw, "id", "bid", "biz", "account_id", "mp_id")
        name = _first_text_value(raw, "name", "title", "nickname", "account", "mp_name") or feed_id
        link = _first_text_value(raw, "feed", "feed_url", "rss", "url", "link")

        if link and (link.endswith(".xml") or link.endswith(".rss") or "/feed/" in link):
            feed_url = urljoin(f"{base}/", link)
        elif feed_id:
            suffix = ".xml"
            feed_url = f"{base}/feed/{quote(feed_id, safe='')}{suffix}"
        else:
            continue

        if token and "?" not in feed_url:
            feed_url = f"{feed_url}?k={quote(token, safe='')}"
        if feed_url in seen:
            continue
        seen.add(feed_url)
        configs.append({"name": name or feed_id or "Wechat2RSS", "url": feed_url, "type": "rss"})
    return configs


def discover_wechat2rss_feeds(app_config: dict[str, Any], timeout: int = 15) -> list[dict[str, str]]:
    settings = wechat2rss_settings(app_config)
    if not bool(settings.get("enabled", False)) or not bool(settings.get("discover_feeds", True)):
        return []
    base_url = str(settings.get("base_url") or "http://127.0.0.1:8001").rstrip("/")
    token = str(settings.get("token") or "").strip()
    json_text = read_url_or_file(wechat2rss_url(app_config, "/list"), timeout=timeout)
    return parse_wechat2rss_list_configs(json_text, base_url=base_url, token=token)


def configured_wechat2rss_feed_configs(app_config: dict[str, Any]) -> list[dict[str, str]]:
    settings = wechat2rss_settings(app_config)
    if not bool(settings.get("enabled", False)):
        return []
    base_url = str(settings.get("base_url") or "http://127.0.0.1:8001").rstrip("/")
    token = str(settings.get("token") or "").strip()
    configs: list[dict[str, str]] = []
    for feed_id in settings.get("subscribe_ids") or []:
        clean_id = str(feed_id or "").strip()
        if not clean_id:
            continue
        name = clean_id
        if isinstance(feed_id, dict):
            clean_id = str(feed_id.get("id") or feed_id.get("bid") or "").strip()
            name = str(feed_id.get("name") or clean_id).strip()
        if clean_id:
            feed_url = f"{base_url}/feed/{quote(clean_id, safe='')}.xml"
            if token:
                feed_url = f"{feed_url}?k={quote(token, safe='')}"
            configs.append({"name": name or clean_id, "url": feed_url, "type": "rss"})
    return configs


def subscribe_wechat2rss_article_url(app_config: dict[str, Any], article_url: str, timeout: int = 15) -> str:
    url = wechat2rss_url(app_config, "/addurl", url=article_url)
    return read_url_or_file(url, timeout=timeout)


def subscribe_wechat2rss_account_id(app_config: dict[str, Any], account_id: str, timeout: int = 15) -> str:
    clean_id = str(account_id or "").strip()
    if not clean_id:
        raise FeedError("Wechat2RSS 订阅 ID 不能为空")
    url = wechat2rss_url(app_config, f"/add/{quote(clean_id, safe='')}")
    return read_url_or_file(url, timeout=timeout)
