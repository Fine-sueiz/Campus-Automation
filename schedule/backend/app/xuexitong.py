from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from .settings import SHANGHAI_TZ


PROVIDER = "xuexitong"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_ENTRY_URL = "https://i.chaoxing.com/"
TASK_KEYWORDS = ("作业", "考试", "测验", "测试", "任务", "提交", "练习", "homework", "assignment", "quiz", "exam")
DEADLINE_KEYWORDS = ("截止", "截至", "截止时间", "截止日期", "结束时间", "到期", "提交时间", "due")
LINK_KEYWORDS = TASK_KEYWORDS + ("待办", "课程", "mooc", "work", "exam", "quiz")
COURSE_LINK_MARKERS = ("stucoursemiddle", "/mycourse/stu", "courseid=", "clazzid=")
COURSE_NAV_IDS = ("nav_100123", "nav_100126", "nav_100127")
IGNORE_BLOCK_MARKERS = (
    "暂无作业",
    "暂无考试",
    "暂无任务",
    "暂无数据",
    "已完成任务点",
    "提交的作业将经过",
    "请勿抄袭",
    "开课时间",
)

DATE_RE = re.compile(
    r"(?<!\d)"
    r"(?:(?P<year>20\d{2})\s*[年./-]\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*[月./-]\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日?"
    r"(?!\d)"
)
TIME_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上|晚)?\s*"
    r"(?P<hour>[01]?\d|2[0-3])\s*(?:[:：点时])\s*"
    r"(?P<minute>[0-5]\d)?\s*(?:分)?"
)
COURSE_RE = re.compile(r"(?:课程|科目|班级)\s*[:：]\s*(?P<name>[^\n\r]{2,80})")
LABEL_RE = re.compile(r"(?:作业|任务|考试|测验|测试|标题|名称)\s*[:：]\s*(?P<name>[^\n\r]{2,100})")


@dataclass(frozen=True)
class ParsedDeadline:
    start_at: datetime
    end_at: datetime
    all_day: bool
    raw_text: str


@dataclass(frozen=True)
class XuexitongItem:
    external_key: str
    item_type: str
    course_name: str
    task_name: str
    start_at: datetime
    end_at: datetime
    all_day: bool
    deadline_text: str
    source_url: str
    raw_text: str

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_at"] = self.start_at.isoformat()
        data["end_at"] = self.end_at.isoformat()
        return data


def normalize_text(value: str) -> str:
    value = value.replace("\u3000", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_text(value: str, limit: int = 160) -> str:
    value = normalize_text(value).replace("\n", " ")
    return value[:limit].strip()


def infer_date(year_text: str | None, month_text: str, day_text: str, today: date | None = None) -> date:
    today = today or datetime.now(SHANGHAI_TZ).date()
    year = int(year_text) if year_text else today.year
    parsed = date(year, int(month_text), int(day_text))
    if not year_text and parsed < today - timedelta(days=30):
        parsed = date(today.year + 1, parsed.month, parsed.day)
    return parsed


def adjust_hour(period: str | None, hour: int) -> int:
    if period in {"下午", "晚上", "晚"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    return hour


def parse_time(match: re.Match[str]) -> time:
    hour = adjust_hour(match.group("period"), int(match.group("hour")))
    minute = int(match.group("minute") or "00")
    return time(hour, minute)


def parse_deadline(text: str, today: date | None = None) -> ParsedDeadline | None:
    areas = deadline_contexts(text)
    for area in areas:
        parsed = parse_deadline_area(area, today=today)
        if parsed:
            return parsed
    return None


def deadline_contexts(text: str) -> list[str]:
    text = normalize_text(text)
    areas: list[str] = []
    for keyword in DEADLINE_KEYWORDS:
        start = 0
        while True:
            index = text.lower().find(keyword.lower(), start)
            if index < 0:
                break
            areas.append(text[max(0, index - 80) : index + 160])
            start = index + len(keyword)
    return areas


def parse_deadline_area(text: str, today: date | None = None) -> ParsedDeadline | None:
    for date_match in DATE_RE.finditer(text):
        try:
            parsed_date = infer_date(
                date_match.group("year"),
                date_match.group("month"),
                date_match.group("day"),
                today=today,
            )
        except ValueError:
            continue

        time_area = text[date_match.end() : date_match.end() + 40]
        time_match = TIME_RE.search(time_area)
        if time_match:
            start_at = datetime.combine(parsed_date, parse_time(time_match), SHANGHAI_TZ)
            return ParsedDeadline(
                start_at=start_at,
                end_at=start_at + timedelta(minutes=30),
                all_day=False,
                raw_text=compact_text(text[max(0, date_match.start() - 30) : date_match.end() + 50]),
            )

        start_at = datetime.combine(parsed_date, time.min, SHANGHAI_TZ)
        return ParsedDeadline(
            start_at=start_at,
            end_at=start_at + timedelta(days=1),
            all_day=True,
            raw_text=compact_text(text[max(0, date_match.start() - 30) : date_match.end() + 50]),
        )
    return None


def detect_item_type(text: str) -> str | None:
    lowered = text.lower()
    exam_positions = [
        lowered.find(keyword)
        for keyword in ("考试", "测验", "测试", "quiz", "exam")
        if lowered.find(keyword) >= 0
    ]
    assignment_positions = [
        lowered.find(keyword)
        for keyword in ("作业", "任务", "提交", "练习", "homework", "assignment")
        if lowered.find(keyword) >= 0
    ]
    if exam_positions and (not assignment_positions or min(exam_positions) < min(assignment_positions)):
        return "考试"
    if assignment_positions:
        return "作业"
    return None


def clean_name(value: str, fallback: str, limit: int = 60) -> str:
    value = re.sub(r"https?://\S+", "", value)
    value = DATE_RE.sub("", value)
    value = TIME_RE.sub("", value)
    value = re.sub(r"(截止|截至|截止时间|截止日期|结束时间|到期|提交时间|课程|科目|班级)\s*[:：]?", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -—｜|:：")
    if len(value) > limit:
        value = value[:limit].strip()
    return value or fallback


def guess_course_name(block_text: str, page_text: str = "") -> str:
    for source in (block_text, page_text):
        course_match = COURSE_RE.search(source)
        if course_match:
            return clean_name(course_match.group("name"), "学习通", limit=40)
        title_match = re.search(r"《(?P<name>[^》]{2,50})》", source)
        if title_match:
            return clean_name(title_match.group("name"), "学习通", limit=40)
    return "学习通"


def guess_task_name(block_text: str, item_type: str) -> str:
    label_match = LABEL_RE.search(block_text)
    if label_match:
        return clean_name(label_match.group("name"), f"学习通{item_type}")

    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if should_ignore_block(line):
            continue
        if any(keyword in lowered for keyword in TASK_KEYWORDS):
            if any(keyword in lowered for keyword in DEADLINE_KEYWORDS):
                continue
            if "课程" in line or "科目" in line:
                continue
            return clean_name(line, f"学习通{item_type}")
    return f"学习通{item_type}"


def external_key_for(source_url: str, item_type: str, course_name: str, task_name: str) -> str:
    raw = "|".join([source_url, item_type, course_name, task_name]).lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_items_from_text(text: str, source_url: str = "", today: date | None = None) -> list[XuexitongItem]:
    text = normalize_text(text)
    if not text:
        return []

    blocks = candidate_blocks(text)

    items: dict[str, XuexitongItem] = {}
    for block in blocks:
        if should_ignore_block(block):
            continue
        item_type = detect_item_type(block)
        if not item_type:
            continue
        deadline = parse_deadline(block, today=today)
        if not deadline:
            continue
        course_name = guess_course_name(block, text)
        task_name = guess_task_name(block, item_type)
        external_key = external_key_for(source_url, item_type, course_name, task_name)
        items[external_key] = XuexitongItem(
            external_key=external_key,
            item_type=item_type,
            course_name=course_name,
            task_name=task_name,
            start_at=deadline.start_at,
            end_at=deadline.end_at,
            all_day=deadline.all_day,
            deadline_text=deadline.raw_text,
            source_url=source_url,
            raw_text=compact_text(block, limit=600),
        )
    return list(items.values())


def candidate_blocks(text: str) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    paragraph_blocks = [
        paragraph
        for paragraph in paragraphs
        if has_task_or_deadline_marker(paragraph)
    ]
    if paragraph_blocks:
        return paragraph_blocks

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if not has_task_or_deadline_marker(line) and not DATE_RE.search(line):
            continue
        start = max(0, index - 4)
        end = min(len(lines), index + 5)
        blocks.append("\n".join(lines[start:end]))
    return blocks or ["\n".join(lines[:12])]


def has_task_or_deadline_marker(text: str) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in TASK_KEYWORDS + DEADLINE_KEYWORDS)


def should_ignore_block(text: str) -> bool:
    return any(marker in text for marker in IGNORE_BLOCK_MARKERS)


def build_event_payload(item: XuexitongItem) -> dict[str, Any]:
    title = f"学习通｜{item.course_name}｜{item.task_name}"[:120]
    notes = "\n".join(
        [
            "同步来源：学习通",
            f"课程：{item.course_name}",
            f"类型：{item.item_type}",
            f"截止信息：{item.deadline_text}",
            f"来源链接：{item.source_url or '未获取'}",
            f"同步ID：{item.external_key}",
            "",
            f"原文片段：{item.raw_text}",
        ]
    )
    return {
        "title": title,
        "start_at": item.start_at.isoformat(),
        "end_at": item.end_at.isoformat(),
        "all_day": item.all_day,
        "category": item.item_type,
        "location": "学习通",
        "notes": notes[:3800],
        "source": PROVIDER,
        "reminder_minutes": 2880 if item.item_type == "考试" else 1440,
        "recurrence": None,
    }


def get_chrome_status(cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {
            "connected": True,
            "cdp_url": cdp_url,
            "browser": data.get("Browser", ""),
            "web_socket_debugger_url": data.get("webSocketDebuggerUrl", ""),
            "error": "",
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "connected": False,
            "cdp_url": cdp_url,
            "browser": "",
            "web_socket_debugger_url": "",
            "error": str(exc),
        }


def looks_like_login_page(url: str, text: str) -> bool:
    lowered_url = url.lower()
    if "login" in lowered_url or "passport2" in lowered_url:
        return True
    login_words = ("登录" in text or "账号" in text) and any(word in text for word in ("密码", "验证码", "手机号"))
    task_words = any(word in text for word in ("作业", "考试", "测验", "课程"))
    return login_words and not task_words


def read_items_from_chrome(
    cdp_url: str = DEFAULT_CDP_URL,
    entry_url: str = DEFAULT_ENTRY_URL,
    max_links: int = 24,
) -> dict[str, Any]:
    chrome_status = get_chrome_status(cdp_url)
    if not chrome_status["connected"]:
        return {
            "status": "chrome_unavailable",
            "needs_login": False,
            "items": [],
            "pages_scanned": 0,
            "error": chrome_status["error"],
        }

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {
            "status": "dependency_missing",
            "needs_login": False,
            "items": [],
            "pages_scanned": 0,
            "error": f"缺少 Playwright 依赖：{exc}",
        }

    items: dict[str, XuexitongItem] = {}
    pages_scanned = 0
    saw_login = False

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = pick_xuexitong_page(context.pages)
            if page is None:
                page = context.new_page()
                page.goto(entry_url, wait_until="domcontentloaded", timeout=15000)

            page.wait_for_timeout(800)
            page_items, page_login = scan_page(page)
            saw_login = saw_login or page_login
            pages_scanned += 1
            for item in page_items:
                items[item.external_key] = item

            links = candidate_links(page)[:max_links]
            for link in links:
                linked_page = context.new_page()
                try:
                    linked_page.goto(link["href"], wait_until="domcontentloaded", timeout=15000)
                    linked_page.wait_for_timeout(800)
                    linked_items, linked_login = scan_page(linked_page)
                    saw_login = saw_login or linked_login
                    pages_scanned += 1
                    for item in linked_items:
                        items[item.external_key] = item
                    if is_course_page(linked_page):
                        for tab_items, tab_login in scan_course_tabs(linked_page):
                            saw_login = saw_login or tab_login
                            pages_scanned += 1
                            for item in tab_items:
                                items[item.external_key] = item
                except PlaywrightTimeoutError:
                    continue
                finally:
                    linked_page.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "browser_error",
            "needs_login": saw_login,
            "items": list(items.values()),
            "pages_scanned": pages_scanned,
            "error": str(exc),
        }

    return {
        "status": "ok",
        "needs_login": saw_login and not items,
        "items": list(items.values()),
        "pages_scanned": pages_scanned,
        "error": "",
    }


def pick_xuexitong_page(pages: list[Any]) -> Any | None:
    for page in reversed(pages):
        try:
            marker = f"{page.url} {page.title()}".lower()
        except Exception:  # noqa: BLE001
            continue
        if any(keyword in marker for keyword in ("chaoxing", "xuexitong", "学习通", "超星")):
            return page
    return pages[-1] if pages else None


def scan_page(page: Any) -> tuple[list[XuexitongItem], bool]:
    all_items: dict[str, XuexitongItem] = {}
    saw_login = False
    frames = getattr(page, "frames", []) or []
    if not frames:
        frames = [page]

    for frame in frames:
        try:
            text = frame.locator("body").inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            text = ""
        url = getattr(frame, "url", "") or getattr(page, "url", "")
        saw_login = saw_login or looks_like_login_page(url, text)
        for item in parse_items_from_text(text, source_url=url):
            all_items[item.external_key] = item
    return list(all_items.values()), saw_login


def is_course_page(page: Any) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    if any(marker in url for marker in ("/mycourse/stu", "stucoursemiddle")):
        return True
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:  # noqa: BLE001
        text = ""
    return "作业" in text and "考试" in text and "章节" in text


def scan_course_tabs(page: Any) -> list[tuple[list[XuexitongItem], bool]]:
    results: list[tuple[list[XuexitongItem], bool]] = []
    for nav_id in COURSE_NAV_IDS:
        try:
            locator = page.locator(f"#{nav_id}")
            if locator.count() != 1:
                continue
            locator.click(timeout=5000)
            page.wait_for_timeout(1200)
            results.append(scan_page(page))
        except Exception:  # noqa: BLE001
            continue
    return results


def candidate_links(page: Any) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    frames = getattr(page, "frames", []) or []
    if not frames:
        frames = [page]
    for frame in frames:
        try:
            frame_links = frame.evaluate(
                """
                () => Array.from(document.links).map((link) => ({
                  href: link.href || '',
                  text: (link.innerText || link.textContent || '').trim()
                }))
                """
            )
            links.extend(frame_links)
        except Exception:  # noqa: BLE001
            continue

    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for link in links:
        href = str(link.get("href") or "")
        text = str(link.get("text") or "")
        marker = f"{href} {text}".lower()
        if not href.startswith("http") or href in seen:
            continue
        if not any(domain in marker for domain in ("chaoxing", "xuexitong")):
            continue
        is_course_link = any(course_marker in marker for course_marker in COURSE_LINK_MARKERS)
        if not is_course_link and not any(keyword.lower() in marker for keyword in LINK_KEYWORDS):
            continue
        seen.add(href)
        candidates.append({"href": href, "text": text[:80]})
    return candidates
