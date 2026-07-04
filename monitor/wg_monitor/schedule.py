from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


DAY_ORDER = [
    ("monday", "周一"),
    ("tuesday", "周二"),
    ("wednesday", "周三"),
    ("thursday", "周四"),
    ("friday", "周五"),
    ("saturday", "周六"),
    ("sunday", "周日"),
]

DAY_ALIASES = {
    "周一": "monday",
    "星期一": "monday",
    "礼拜一": "monday",
    "周二": "tuesday",
    "星期二": "tuesday",
    "礼拜二": "tuesday",
    "周三": "wednesday",
    "星期三": "wednesday",
    "礼拜三": "wednesday",
    "周四": "thursday",
    "星期四": "thursday",
    "礼拜四": "thursday",
    "周五": "friday",
    "星期五": "friday",
    "礼拜五": "friday",
    "周六": "saturday",
    "星期六": "saturday",
    "礼拜六": "saturday",
    "周日": "sunday",
    "周天": "sunday",
    "星期日": "sunday",
    "星期天": "sunday",
    "礼拜日": "sunday",
    "礼拜天": "sunday",
}


@dataclass(frozen=True)
class TimeBlock:
    day: str
    start: int
    end: int
    label: str = ""

    def overlaps_or_contains(self, other: "TimeBlock") -> bool:
        return self.day == other.day and self.start <= other.start and self.end >= other.end


def parse_time(value: str) -> int:
    text = str(value).strip()
    match = re.fullmatch(r"([01]?\d|2[0-3])[:：点]([0-5]\d)?", text)
    if not match:
        raise ValueError(f"时间格式必须类似 08:00，目前是：{value}")
    hour = int(match.group(1))
    minute = int(match.group(2) or "00")
    return hour * 60 + minute


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def merge_blocks(blocks: list[TimeBlock]) -> list[TimeBlock]:
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda block: (block.day, block.start, block.end))
    merged: list[TimeBlock] = [sorted_blocks[0]]
    for block in sorted_blocks[1:]:
        last = merged[-1]
        if block.day == last.day and block.start <= last.end:
            merged[-1] = TimeBlock(last.day, last.start, max(last.end, block.end), last.label)
        else:
            merged.append(block)
    return merged


def build_busy_blocks(schedule_config: dict[str, Any]) -> list[TimeBlock]:
    days = schedule_config.get("days") or {}
    blocks: list[TimeBlock] = []
    for day_key, _day_label in DAY_ORDER:
        day_data = days.get(day_key) or {}
        for item in day_data.get("busy") or []:
            start = parse_time(str(item["start"]))
            end = parse_time(str(item["end"]))
            if end <= start:
                raise ValueError(f"{day_key} 的课程结束时间必须晚于开始时间：{item}")
            blocks.append(TimeBlock(day=day_key, start=start, end=end, label=str(item.get("name", ""))))
    return merge_blocks(blocks)


def compute_free_blocks(schedule_config: dict[str, Any]) -> dict[str, list[TimeBlock]]:
    day_start = parse_time(str(schedule_config.get("day_start", "08:00")))
    day_end = parse_time(str(schedule_config.get("day_end", "22:00")))
    if day_end <= day_start:
        raise ValueError("day_end 必须晚于 day_start")

    busy_by_day: dict[str, list[TimeBlock]] = {day: [] for day, _ in DAY_ORDER}
    for block in build_busy_blocks(schedule_config):
        clipped_start = max(day_start, block.start)
        clipped_end = min(day_end, block.end)
        if clipped_end > clipped_start:
            busy_by_day[block.day].append(TimeBlock(block.day, clipped_start, clipped_end, block.label))

    free_by_day: dict[str, list[TimeBlock]] = {}
    for day, _label in DAY_ORDER:
        cursor = day_start
        free: list[TimeBlock] = []
        for busy in merge_blocks(busy_by_day[day]):
            if busy.start > cursor:
                free.append(TimeBlock(day, cursor, busy.start))
            cursor = max(cursor, busy.end)
        if cursor < day_end:
            free.append(TimeBlock(day, cursor, day_end))
        free_by_day[day] = free
    return free_by_day


def format_free_blocks(free_by_day: dict[str, list[TimeBlock]]) -> str:
    lines: list[str] = []
    for day, label in DAY_ORDER:
        blocks = free_by_day.get(day) or []
        if not blocks:
            lines.append(f"{label}：无明显空闲")
            continue
        ranges = "，".join(f"{format_time(block.start)}-{format_time(block.end)}" for block in blocks)
        lines.append(f"{label}：{ranges}")
    return "\n".join(lines)


def find_matching_free_blocks(
    free_by_day: dict[str, list[TimeBlock]], required_windows: list[TimeBlock]
) -> list[TimeBlock]:
    matches: list[TimeBlock] = []
    for required in required_windows:
        for free in free_by_day.get(required.day, []):
            if free.overlaps_or_contains(required):
                matches.append(required)
                break
    return matches


def format_time_windows(windows: list[TimeBlock]) -> str:
    label_by_day = dict(DAY_ORDER)
    return "\n".join(
        f"{label_by_day.get(window.day, window.day)}：{format_time(window.start)}-{format_time(window.end)}"
        for window in windows
    )
