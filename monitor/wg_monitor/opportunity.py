from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .extraction import (
    extract_deadline,
    extract_position_title,
    extract_time_windows,
    normalize_text,
)
from .feed import stable_id
from .schedule import (
    TimeBlock,
    compute_free_blocks,
    find_matching_free_blocks,
    format_free_blocks,
    format_time_windows,
)
from .timeparse import DATE_TIME_RE


CATEGORY_KEYWORDS = {
    "volunteer": ["志愿活动", "志愿服务", "志愿者", "义工", "志愿时长", "综测", "公益"],
    "work_study": ["勤工助学", "助学岗位", "岗位招聘", "学生助理", "助管", "助教"],
    "competition": ["竞赛", "比赛", "挑战赛", "创新创业", "大赛"],
    "lecture": ["讲座", "报告会", "宣讲会", "分享会", "沙龙"],
    "scholarship": ["奖学金", "助学金", "资助", "评选", "申报"],
}

CATEGORY_LABELS = {
    "volunteer": "志愿活动",
    "work_study": "勤工助学",
    "competition": "竞赛机会",
    "lecture": "讲座活动",
    "scholarship": "奖助学金",
    "other": "其他机会",
}

FOCUS_KEYWORDS = ["报名", "招募", "申请", "参加", "通知", "征集", "截止", "名额", "下学期", "本学期"]
SIGNUP_HINTS = ["wjx", "问卷", "form", "signup", "jinshuju", "金数据", "腾讯问卷", "v.wjx.cn", "www.wjx.cn"]
URL_RE = re.compile(r"https?://[^\s\"'<>，。；、)）]+")
LOCATION_RE = re.compile(r"(?:活动地点|服务地点|工作地点|地点|地址)[:：\s]*([^\n。；;]{2,80})")
TIME_TEXT_RE = re.compile(
    r"(?:活动时间|服务时间|工作时间|报名时间|时间)[:：\s]*([^\n。；;]{4,100})"
)


@dataclass(frozen=True)
class OpportunityAnalysis:
    id: str
    is_target: bool
    category: str
    category_label: str
    title: str
    source_name: str
    article_item_id: str
    article_url: str
    signup_url: str
    activity_time: str
    deadline: str
    location: str
    schedule_status: str
    free_time_text: str
    matched_time_text: str
    raw_text: str
    keyword_hits: list[str]
    reasons: list[str]

    def to_db_payload(self, status: str = "pending_decision", message_id: str = "") -> dict[str, Any]:
        return {
            "id": self.id,
            "article_item_id": self.article_item_id,
            "category": self.category,
            "title": self.title,
            "source_name": self.source_name,
            "article_url": self.article_url,
            "signup_url": self.signup_url,
            "activity_time": self.activity_time,
            "deadline": self.deadline,
            "location": self.location,
            "schedule_status": self.schedule_status,
            "free_time_text": self.free_time_text,
            "matched_time_text": self.matched_time_text,
            "raw_text": self.raw_text,
            "status": status,
            "feishu_message_id": message_id,
        }


def classify_opportunity(title: str, text: str) -> tuple[str, list[str]]:
    combined = f"{title}\n{text}"
    best_category = "other"
    best_hits: list[str] = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = [word for word in keywords if word in combined]
        if len(hits) > len(best_hits):
            best_category = category
            best_hits = hits
    focus_hits = [word for word in FOCUS_KEYWORDS if word in combined]
    return best_category, list(dict.fromkeys(best_hits + focus_hits))


def extract_signup_url(text: str, fallback: str = "") -> str:
    urls = [url.rstrip(".") for url in URL_RE.findall(text)]
    if fallback and fallback not in urls:
        urls.append(fallback)
    for url in urls:
        lowered = url.lower()
        if any(hint in lowered or hint in url for hint in SIGNUP_HINTS):
            return url
    return urls[0] if urls else fallback


def extract_location(text: str) -> str:
    match = LOCATION_RE.search(text)
    if match:
        return match.group(1).strip(" ：:，,")
    for line in text.splitlines():
        if "地点" in line and len(line.strip()) <= 100:
            return line.strip()
    return ""


def extract_activity_time_text(text: str) -> str:
    match = TIME_TEXT_RE.search(text)
    if match:
        return match.group(1).strip()
    match = DATE_TIME_RE.search(text)
    return match.group(1).strip() if match else ""


def schedule_status_for(
    schedule_config: dict[str, Any],
    time_windows: list[TimeBlock],
) -> tuple[str, str, str]:
    free_by_day = compute_free_blocks(schedule_config)
    free_text = format_free_blocks(free_by_day)
    if not time_windows:
        return "unknown_time", free_text, ""
    matches = find_matching_free_blocks(free_by_day, time_windows)
    if matches:
        return "available", free_text, format_time_windows(matches)
    return "conflict", free_text, ""


def analyse_opportunity(
    title: str,
    text: str,
    article_url: str,
    source_name: str,
    article_item_id: str,
    app_config: dict[str, Any],
    schedule_config: dict[str, Any],
) -> OpportunityAnalysis:
    cleaned_text = normalize_text(text)
    combined_text = f"{title}\n{cleaned_text}"
    category, hits = classify_opportunity(title, cleaned_text)
    configured = (app_config.get("opportunity") or {}).get("extra_keywords") or []
    configured_hits = [str(word) for word in configured if str(word) and str(word) in combined_text]
    hits = list(dict.fromkeys(hits + configured_hits))

    opportunity_config = app_config.get("opportunity") or {}
    is_target = (category != "other" and len(hits) >= 2) or len(configured_hits) >= 1
    enabled_categories = [
        str(item).strip()
        for item in opportunity_config.get("enabled_categories", [])
        if str(item).strip()
    ]
    required_any = [
        str(item).strip()
        for item in opportunity_config.get("required_any", [])
        if str(item).strip()
    ]
    signup_url = extract_signup_url(cleaned_text, fallback=article_url)
    time_windows = extract_time_windows(cleaned_text)
    schedule_status, free_text, matched_text = schedule_status_for(schedule_config, time_windows)
    activity_time = extract_activity_time_text(cleaned_text)
    deadline = extract_deadline(cleaned_text)
    location = extract_location(cleaned_text)
    position_title = extract_position_title(title, cleaned_text) or title

    reasons: list[str] = []
    if enabled_categories and category not in enabled_categories:
        is_target = False
        reasons.append(f"机会类别不在监测范围：{category}")
    if required_any and not any(word in combined_text for word in required_any):
        is_target = False
        reasons.append(f"未命中必需关键词：{'、'.join(required_any)}")
    if not is_target:
        reasons.append("未命中校园机会类别关键词")
    if schedule_status == "unknown_time":
        reasons.append("未识别到可用于课表匹配的星期+时间段")
    if not signup_url:
        reasons.append("未识别到报名链接")

    opportunity_id = stable_id(article_item_id, article_url, position_title, category)
    return OpportunityAnalysis(
        id=opportunity_id,
        is_target=is_target,
        category=category,
        category_label=CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
        title=position_title[:120],
        source_name=source_name,
        article_item_id=article_item_id,
        article_url=article_url,
        signup_url=signup_url,
        activity_time=activity_time,
        deadline=deadline,
        location=location,
        schedule_status=schedule_status,
        free_time_text=free_text,
        matched_time_text=matched_text,
        raw_text=cleaned_text[:12000],
        keyword_hits=hits,
        reasons=reasons,
    )
