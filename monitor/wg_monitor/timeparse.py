from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

from .schedule import DAY_ALIASES, TimeBlock, parse_time


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 课表匹配使用的星期+时间段解析，保持 extraction.py 原语义：不识别“中午”。
TIME_RANGE_RE = re.compile(
    r"(?P<start>[01]?\d|2[0-3])\s*[:：点]\s*(?P<start_min>[0-5]\d)?\s*"
    r"(?:-|—|–|~|至|到)\s*"
    r"(?P<end>[01]?\d|2[0-3])\s*[:：点]\s*(?P<end_min>[0-5]\d)?"
)
DAY_RE = re.compile(r"(周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天])")
DAY_RANGE_RE = re.compile(
    r"(周|星期|礼拜)(?P<start>[一二三四五六日天])\s*(?:至|到|-|—|–|~)\s*(?:周|星期|礼拜)?(?P<end>[一二三四五六日天])"
)
DEADLINE_RE = re.compile(
    r"(?:截止|报名|申请|投递|发送).{0,12}?"
    r"((?:20\d{2}[年/-])?\d{1,2}[月/-]\d{1,2}日?(?:\s*(?:前|之前|截止))?)"
)

# 日历同步使用的显式日期+时间段解析，保持 calendar_sync.py 原语义：识别“中午”。
DATE_RE = re.compile(r"(?<![:：\d])(?:(?P<year>20\d{2})[年/-])?(?P<month>\d{1,2})[月/-](?P<day>\d{1,2})日?")
CALENDAR_TIME_RANGE_RE = re.compile(
    r"(?P<start_period>上午|下午|晚上|中午)?\s*"
    r"(?P<start_hour>[01]?\d|2[0-3])\s*[:：点]\s*(?P<start_min>[0-5]\d)?\s*"
    r"(?:-|—|–|~|至|到)\s*"
    r"(?P<end_period>上午|下午|晚上|中午)?\s*"
    r"(?P<end_hour>[01]?\d|2[0-3])\s*[:：点]\s*(?P<end_min>[0-5]\d)?"
)

# 机会摘要文本使用的时间日期片段解析，保持 opportunity.py 原语义：不识别“中午”。
DATE_TIME_RE = re.compile(
    r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日(?:\s*(?:周[一二三四五六日天])?)?"
    r"(?:\s*(?:上午|下午|晚上)?\s*\d{1,2}[:：点]\d{0,2}\s*(?:-|—|–|~|至|到)\s*"
    r"(?:上午|下午|晚上)?\s*\d{1,2}[:：点]\d{0,2})?)"
)

DAY_CHARS = "一二三四五六日天"
DAY_CHAR_TO_KEY = {
    "一": "monday",
    "二": "tuesday",
    "三": "wednesday",
    "四": "thursday",
    "五": "friday",
    "六": "saturday",
    "日": "sunday",
    "天": "sunday",
}


def normalize_time_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_deadline(text: str) -> str:
    match = DEADLINE_RE.search(text)
    return match.group(1).strip() if match else ""


def expand_day_range(prefix: str, start_char: str, end_char: str) -> list[str]:
    start_index = DAY_CHARS.index(start_char)
    end_index = DAY_CHARS.index(end_char)
    if end_char == "天":
        end_index = DAY_CHARS.index("日")
    if start_index > end_index:
        return []
    days = []
    for char in DAY_CHARS[start_index : end_index + 1]:
        if char == "天":
            continue
        day_key = DAY_CHAR_TO_KEY.get(char)
        if day_key and day_key not in days:
            days.append(day_key)
    return days


def adjust_time_for_period(context: str, hour: int) -> int:
    if ("下午" in context or "晚上" in context) and 1 <= hour <= 11:
        return hour + 12
    return hour


def extract_time_windows(text: str) -> list[TimeBlock]:
    normalized = normalize_time_text(text)
    windows: list[TimeBlock] = []

    for time_match in TIME_RANGE_RE.finditer(normalized):
        context_start = max(0, time_match.start() - 28)
        context = normalized[context_start : time_match.start()]
        day_keys: list[str] = []

        for range_match in DAY_RANGE_RE.finditer(context):
            day_keys.extend(
                expand_day_range(range_match.group(1), range_match.group("start"), range_match.group("end"))
            )

        for day_match in DAY_RE.finditer(context):
            day_key = DAY_ALIASES.get(day_match.group(1))
            if day_key:
                day_keys.append(day_key)

        day_keys = list(dict.fromkeys(day_keys))
        if not day_keys:
            continue

        start_hour = adjust_time_for_period(context, int(time_match.group("start")))
        end_hour = adjust_time_for_period(context, int(time_match.group("end")))
        start_value = f"{start_hour}:{time_match.group('start_min') or '00'}"
        end_value = f"{end_hour}:{time_match.group('end_min') or '00'}"
        try:
            start = parse_time(start_value)
            end = parse_time(end_value)
        except ValueError:
            continue
        if end <= start:
            continue

        for day_key in day_keys:
            windows.append(TimeBlock(day=day_key, start=start, end=end))

    unique: dict[tuple[str, int, int], TimeBlock] = {}
    for window in windows:
        unique[(window.day, window.start, window.end)] = window
    return list(unique.values())


def infer_date(year_text: str | None, month_text: str, day_text: str, today: date | None = None) -> date:
    today = today or datetime.now(SHANGHAI_TZ).date()
    year = int(year_text) if year_text else today.year
    parsed = date(year, int(month_text), int(day_text))
    if not year_text and parsed < today - timedelta(days=30):
        parsed = date(today.year + 1, parsed.month, parsed.day)
    return parsed


def adjust_hour(period: str, hour: int) -> int:
    if period in {"下午", "晚上"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    return hour


def parse_time_range(match: re.Match[str]) -> tuple[time, time] | None:
    start_period = match.group("start_period") or ""
    end_period = match.group("end_period") or start_period
    start_hour = adjust_hour(start_period, int(match.group("start_hour")))
    end_hour = adjust_hour(end_period, int(match.group("end_hour")))
    start_min = int(match.group("start_min") or "00")
    end_min = int(match.group("end_min") or "00")
    start = time(start_hour, start_min)
    end = time(end_hour, end_min)
    if end <= start and end_hour <= 12:
        try:
            end = time(end_hour + 12, end_min)
        except ValueError:
            return None
    if end <= start:
        return None
    return start, end


def parse_explicit_activity_window(text: str) -> tuple[datetime, datetime] | None:
    for date_match in DATE_RE.finditer(text):
        search_window = text[date_match.end() : date_match.end() + 120]
        time_match = CALENDAR_TIME_RANGE_RE.search(search_window)
        if not time_match:
            continue
        parsed_range = parse_time_range(time_match)
        if not parsed_range:
            continue
        try:
            parsed_date = infer_date(date_match.group("year"), date_match.group("month"), date_match.group("day"))
        except ValueError:
            continue
        start_time, end_time = parsed_range
        start_at = datetime.combine(parsed_date, start_time, SHANGHAI_TZ)
        end_at = datetime.combine(parsed_date, end_time, SHANGHAI_TZ)
        return start_at, end_at
    return None


def parse_first_date(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        return infer_date(match.group("year"), match.group("month"), match.group("day"))
    except ValueError:
        return None
