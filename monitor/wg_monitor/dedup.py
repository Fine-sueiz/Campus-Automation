"""内容级去重：微信 URL 归一化 + 标题指纹。

feed 条目级去重（articles 表按 item_id）防不住三类重复：
1. Wechat2RSS 重建后同一文章 guid 变化；
2. 同一文章的两种微信链接形态（/s/<id> 与 /s?__biz=...&sn=...）；
3. 同一活动被多个公众号转发（链接不同、标题几乎相同）。

本模块为一条内容生成若干指纹键（url:xxx / title:xxx），
流水线在检出目标机会时先查 content_fingerprints 表：
时间窗内已有同指纹 → 合并到已有机会，不再重复提醒。
原始文章记录不合并，全部保留在 articles 表。
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .feed import stable_id

# 微信长链接中标识文章身份的参数；其余(chksm/scene/sharer_*等)全是分享追踪参数
WECHAT_IDENTITY_PARAMS = ("__biz", "mid", "idx", "sn")

# 常见追踪参数（通用网页）
TRACKING_PARAM_PREFIXES = ("utm_", "share", "from", "src")

# 标题规范化：去掉标点、空白、装饰符号后比较
_TITLE_NOISE_RE = re.compile(
    r"[\s　！!？?。．.，,、；;：:“”\"'‘’（）()\[\]【】《》〈〉<>|/\\—\-–~·•…#*＋+➕🔥✨📣📢❗️⭐]+"
)
# 常见转发前缀（去掉后再比较）
_FORWARD_PREFIX_RE = re.compile(r"^(转发|转|扩散|速看|重要|通知|急)+")

DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_TITLE_CHARS = 8


def dedup_config(app_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(app_config.get("dedup") or {})
    config.setdefault("enabled", True)
    config.setdefault("window_days", DEFAULT_WINDOW_DAYS)
    config.setdefault("min_title_chars", DEFAULT_MIN_TITLE_CHARS)
    return config


def canonical_article_url(url: str) -> str:
    """归一化文章 URL：微信链接去掉追踪参数，其余链接去 fragment/utm。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    host = parsed.netloc.lower()

    if host.endswith("mp.weixin.qq.com"):
        path = parsed.path.rstrip("/")
        if path.startswith("/s/"):
            # 短链形态：路径本身就是文章 ID，query 全是追踪参数
            return f"https://mp.weixin.qq.com{path}"
        if path == "/s":
            params = dict(parse_qsl(parsed.query, keep_blank_values=False))
            identity = [(key, params[key]) for key in WECHAT_IDENTITY_PARAMS if params.get(key)]
            # 仅有 __biz 只能定位公众号、定位不了文章，必须有 sn 或 mid 才能当文章指纹，
            # 否则同一公众号的不同文章会被误合并
            if identity and (params.get("sn") or params.get("mid")):
                return f"https://mp.weixin.qq.com/s?{urlencode(identity)}"
        return f"https://mp.weixin.qq.com{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(key.lower().startswith(prefix) for prefix in TRACKING_PARAM_PREFIXES)
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            host,
            parsed.path,
            "",
            urlencode(query_pairs),
            "",  # fragment 丢弃
        )
    )


def normalized_title(title: str) -> str:
    text = unescape(str(title or ""))
    text = _TITLE_NOISE_RE.sub("", text)
    text = _FORWARD_PREFIX_RE.sub("", text)
    return text.casefold()


def dedup_keys(title: str, url: str, *, min_title_chars: int = DEFAULT_MIN_TITLE_CHARS) -> list[str]:
    """一条内容的指纹键列表。键之间任一命中即视为同一内容。"""
    keys: list[str] = []
    canonical = canonical_article_url(url)
    if canonical:
        keys.append(f"url:{stable_id(canonical)}")
    cleaned = normalized_title(title)
    # 标题太短（如“通知”“报名啦”）没有区分度，不参与跨号合并
    if len(cleaned) >= max(1, min_title_chars):
        keys.append(f"title:{stable_id(cleaned)}")
    return keys
