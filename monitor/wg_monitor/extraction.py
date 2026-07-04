from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any

from .schedule import TimeBlock
from .timeparse import (
    DAY_CHARS,
    DAY_CHAR_TO_KEY,
    DAY_RANGE_RE,
    DAY_RE,
    DEADLINE_RE,
    TIME_RANGE_RE,
    adjust_time_for_period,
    expand_day_range,
    extract_deadline,
    extract_time_windows,
)


EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")


@dataclass(frozen=True)
class ArticleAnalysis:
    title: str
    text: str
    is_target: bool
    keyword_hits: list[str]
    emails: list[str]
    position_title: str
    deadline: str
    time_windows: list[TimeBlock]
    reasons: list[str]


def html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ModuleNotFoundError:
        text = re.sub(r"<[^>]+>", " ", html)
        return normalize_text(unescape(text))

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return normalize_text(soup.get_text("\n"))


def normalize_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_emails(text: str) -> list[str]:
    return sorted({match.group(0).strip(".,;:，。；：") for match in EMAIL_RE.finditer(text)})


def extract_position_title(title: str, text: str) -> str:
    title = normalize_text(title)
    if any(word in title for word in ("勤工助学", "助学", "岗位", "招聘")):
        return title[:80]

    for line in normalize_text(text).splitlines():
        cleaned = line.strip()
        if 4 <= len(cleaned) <= 80 and any(word in cleaned for word in ("勤工助学", "岗位", "招聘")):
            return cleaned
    return title[:80]


def collect_keyword_hits(title: str, text: str, keywords: dict[str, Any]) -> list[str]:
    combined = f"{title}\n{text}"
    configured = []
    configured.extend(keywords.get("required_any") or [])
    configured.extend(keywords.get("focus_any") or [])
    default_keywords = ["勤工助学", "岗位", "招聘", "报名", "下学期", "申请", "助学"]
    words = [str(word) for word in configured or default_keywords]
    return [word for word in dict.fromkeys(words) if word and word in combined]


def analyse_article(title: str, text: str, app_config: dict[str, Any]) -> ArticleAnalysis:
    monitor_config = app_config.get("monitor") or {}
    safety_config = app_config.get("safety") or {}
    keywords = monitor_config.get("keywords") or {}
    min_hits = int(safety_config.get("min_keyword_hits", 2))

    cleaned_text = normalize_text(text)
    hits = collect_keyword_hits(title, cleaned_text, keywords)
    has_core = "勤工助学" in f"{title}\n{cleaned_text}" or (
        "助学" in f"{title}\n{cleaned_text}" and "岗位" in f"{title}\n{cleaned_text}"
    )
    is_target = has_core and len(hits) >= min_hits
    emails = extract_emails(cleaned_text)
    position_title = extract_position_title(title, cleaned_text)
    deadline = extract_deadline(cleaned_text)
    time_windows = extract_time_windows(cleaned_text)

    reasons: list[str] = []
    if not is_target:
        reasons.append(f"关键词不足或缺少勤工助学核心词，命中：{', '.join(hits) or '无'}")
    if not emails:
        reasons.append("未提取到岗位邮箱")
    if len(emails) > 1:
        reasons.append(f"提取到多个邮箱：{', '.join(emails)}")
    if not position_title:
        reasons.append("未识别到岗位标题")

    return ArticleAnalysis(
        title=title,
        text=cleaned_text,
        is_target=is_target,
        keyword_hits=hits,
        emails=emails,
        position_title=position_title,
        deadline=deadline,
        time_windows=time_windows,
        reasons=reasons,
    )
