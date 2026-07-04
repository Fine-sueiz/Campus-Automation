import argparse
import asyncio
import hashlib
import html as html_lib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from ml_question_model import QuestionClassifier


COMPLEX_VERIFICATION_TEXT = [
    "验证码",
    "滑块",
    "拖动",
    "人机验证",
    "安全验证",
    "短信验证",
    "二维码",
    "captcha",
    "recaptcha",
    "hcaptcha",
    "geetest",
]
WECHAT_ONLY_TEXT = [
    "该表单只限在微信里填写",
    "微信扫一扫填写表单",
    "请在微信中打开",
    "请使用微信扫码填写",
]

COMPLEX_VERIFICATION_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='captcha']",
    "[class*='captcha' i]",
    "[id*='captcha' i]",
    "[class*='slider' i]",
    "[id*='slider' i]",
    "[class*='geetest' i]",
    "[id*='geetest' i]",
]

QUESTION_CONTAINERS = ".field.ui-field-contain, .div_question, fieldset, li, section, article, .question, .field, .form-item, .ant-form-item, .el-form-item"
ML_QUESTION_CONTAINERS = "fieldset, li, section, article, .question, .field, .form-item, .ant-form-item, .el-form-item"
TEXT_INPUTS = "input:not([type]), input[type='text'], input[type='tel'], input[type='email'], input[type='number'], textarea"
SUBMIT_ERROR_TEXT = ["请选择", "请填写", "请上传", "不能为空", "未完成"]
SUBMIT_PROBLEM_TEXT = ["检测程序", "人机验证", "安全验证", "提交失败", "提交异常", "系统繁忙", "请勿重复提交"]
DEFAULT_LLM_VALIDATION_KEYWORDS = ["验证", "请选择", "此题", "本题", "为了验证", "计算", "等于", "多少", "机器人"]
DEFAULT_TERMS_AGREEMENT_KEYWORDS = ["我已阅读并同意", "已阅读并同意", "阅读并同意", "我同意", "同意", "用户协议", "服务协议", "隐私政策", "协议条款", "同意协议"]
DEFAULT_TERMS_AGREEMENT_NEGATIVE_KEYWORDS = ["不同意", "拒绝", "取消", "不同意协议"]
DEFAULT_TERMS_AGREEMENT_BUTTON_TEXTS = ["同意并继续", "同意并提交", "同意并确认", "我已阅读并同意", "同意"]
DEFAULT_PRIVACY_KEYWORDS = [
    "姓名",
    "名字",
    "学号",
    "学生编号",
    "学生证号",
    "手机号",
    "手机号码",
    "联系电话",
    "联系方式",
    "微信",
    "微信号",
    "身份证",
    "证件号",
    "邮箱",
    "邮件",
    "地址",
    "宿舍",
    "班级",
    "学院",
    "院系",
]
LLM_ANSWER_TYPES = {"text", "single", "radio", "multiple", "checkbox", "skip"}
LLM_ROUTE_DECISIONS = {"local", "llm_answer", "skip"}
DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M")
TIME_ONLY_FORMATS = ("%H:%M:%S", "%H:%M")
DEFAULT_QR_CODE_KEYWORDS = [
    "二维码",
    "群聊",
    "微信群",
    "QQ群",
    "入群",
    "扫码",
    "扫一扫",
    "qr",
    "qrcode",
    "qr code",
]
QR_CODE_SELECTORS = "img, canvas, svg, [style*='background-image']"


@dataclass
class FillResult:
    label: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass
class LocalAnswer:
    local_id: str
    label: str
    answer_type: str
    value: Any
    keywords: list[str]
    source: str


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        config = json.load(f)
    if not config.get("url") and not config.get("questionnaires") and not config.get("urls"):
        raise ValueError("config.json requires url, urls, or questionnaires")
    return config


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def questionnaire_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    base = {key: value for key, value in config.items() if key not in {"questionnaires", "urls"}}

    entries = config.get("questionnaires")
    if entries is None:
        entries = config.get("urls")
    if entries is None:
        entries = [{"url": config.get("url")}]
    if not isinstance(entries, list) or not entries:
        raise ValueError("questionnaires/urls must be a non-empty list")

    resolved: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        if isinstance(entry, str):
            item = {"url": entry}
        elif isinstance(entry, dict):
            item = dict(entry)
        else:
            raise ValueError(f"questionnaire entry #{index} must be a URL string or object")
        item_config = deep_merge(base, item)
        if not item_config.get("url"):
            raise ValueError(f"questionnaire entry #{index} missing url")
        item_config.setdefault("label", f"questionnaire #{index}")
        resolved.append(item_config)
    return resolved


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def contains_text(haystack: str, needle: str) -> bool:
    return normalize(needle) in normalize(haystack)


def answer_keywords(answer: dict[str, Any]) -> list[str]:
    raw = answer.get("keywords") or answer.get("question_keywords")
    if raw is None:
        raw = answer.get("question_contains") or answer.get("question")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def config_timezone(config: dict[str, Any]) -> tuple[str, timezone | ZoneInfo]:
    timezone_name = config.get("timezone", "Asia/Shanghai")
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name in {"Asia/Shanghai", "UTC+08:00", "+08:00"}:
            tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
        else:
            raise
    return str(timezone_name), tz


def parse_schedule_datetime(value: Any, tz: timezone | ZoneInfo, field_name: str) -> datetime:
    text = str(value or "").strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue

    for fmt in TIME_ONLY_FORMATS:
        try:
            parsed_time = datetime.strptime(text, fmt).time()
            now = datetime.now(tz)
            return datetime.combine(now.date(), parsed_time).replace(tzinfo=tz)
        except ValueError:
            continue

    raise ValueError(f"{field_name} must be YYYY-MM-DD HH:MM:SS or HH:MM, got: {text}")


def first_config_value(config: dict[str, Any], keys: list[str]) -> tuple[str, Any] | None:
    for key in keys:
        value = config.get(key)
        if value is not None and str(value).strip() != "":
            return key, value
    return None


def nonnegative_seconds(config: dict[str, Any], keys: list[str], default: float = 0.0) -> float:
    item = first_config_value(config, keys)
    if item is None:
        return default
    key, value = item
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative number, got: {value}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"{key} must be a non-negative number, got: {value}")
    return seconds


def format_seconds(seconds: float) -> str:
    return f"{seconds:g}"


async def wait_until_schedule_time(config: dict[str, Any], keys: list[str], action_label: str) -> None:
    item = first_config_value(config, keys)
    if item is None:
        return

    key, value = item
    timezone_name, tz = config_timezone(config)
    target = parse_schedule_datetime(value, tz, key)
    now = datetime.now(tz)
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        print(f"[time] configured {key} has passed: {value}; continuing now")
        return

    print(f"[time] waiting until {value} ({timezone_name}) before {action_label}")
    while seconds > 0:
        chunk = min(seconds, 30)
        await asyncio.sleep(chunk)
        now = datetime.now(tz)
        seconds = (target - now).total_seconds()
        if seconds > 0:
            print(f"[time] {int(seconds)}s remaining before {action_label}")


async def wait_until_start(config: dict[str, Any]) -> None:
    await wait_until_schedule_time(config, ["start_time"], "start filling")


async def wait_until_submit_time(config: dict[str, Any]) -> None:
    await wait_until_schedule_time(config, ["submit_time", "submit_at"], "submit")


async def wait_min_submit_after_start(config: dict[str, Any], questionnaire_started_at: float) -> None:
    seconds = nonnegative_seconds(config, ["min_submit_after_start_seconds", "submit_after_start_seconds"], 0.0)
    if seconds <= 0:
        return

    elapsed = time.monotonic() - questionnaire_started_at
    remaining = seconds - elapsed
    if remaining > 0:
        print(
            f"\n[submit] waiting {format_seconds(remaining)}s so at least "
            f"{format_seconds(seconds)}s pass after start"
        )
        await asyncio.sleep(remaining)


async def visible_count(locator: Locator) -> int:
    count = await locator.count()
    visible = 0
    for i in range(min(count, 20)):
        try:
            if await locator.nth(i).is_visible(timeout=200):
                visible += 1
        except PlaywrightTimeoutError:
            pass
    return visible


async def detect_complex_verification(page: Page) -> list[str]:
    reasons: list[str] = []

    for selector in COMPLEX_VERIFICATION_SELECTORS:
        try:
            if await visible_count(page.locator(selector)):
                reasons.append(f"visible selector: {selector}")
        except Exception:
            continue

    body_text = normalize(await page.locator("body").inner_text(timeout=3000))
    for keyword in COMPLEX_VERIFICATION_TEXT:
        if normalize(keyword) in body_text:
            reasons.append(f"text keyword: {keyword}")

    return reasons


async def detect_wechat_only_page(page: Page) -> list[str]:
    try:
        body_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""
    normalized_body = normalize(body_text)
    return [keyword for keyword in WECHAT_ONLY_TEXT if normalize(keyword) in normalized_body]


async def find_question_container(page: Page, keywords: list[str]) -> Locator | None:
    matches: list[tuple[int, Locator]] = []
    for keyword in keywords:
        candidates = page.locator(QUESTION_CONTAINERS).filter(has_text=re.compile(re.escape(keyword), re.I))
        count = await candidates.count()
        for i in range(min(count, 100)):
            candidate = candidates.nth(i)
            try:
                if await candidate.is_visible(timeout=200):
                    text = await candidate.inner_text(timeout=500)
                    if any(contains_text(text, item) for item in keywords):
                        controls = await candidate.locator("input, textarea, select, [role='radio'], [role='checkbox']").count()
                        if controls:
                            matches.append((len(normalize(text)), candidate))
            except Exception:
                continue
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


async def fill_text_in_container(container: Locator, value: str) -> bool:
    inputs = container.locator(TEXT_INPUTS)
    count = await inputs.count()
    for i in range(count):
        field = inputs.nth(i)
        try:
            if await field.is_visible(timeout=300) and await field.is_enabled(timeout=300):
                await field.fill(str(value))
                return True
        except Exception:
            continue
    return False


async def click_option(container: Locator, option_text: str) -> bool:
    option_pattern = re.compile(rf"^\s*{re.escape(str(option_text))}\s*$", re.I)
    exact = container.get_by_text(option_pattern)
    count = await exact.count()
    for i in range(min(count, 20)):
        item = exact.nth(i)
        try:
            if await item.is_visible(timeout=300):
                await item.click()
                return True
        except Exception:
            continue

    loose = container.get_by_text(str(option_text), exact=False)
    count = await loose.count()
    for i in range(min(count, 20)):
        item = loose.nth(i)
        try:
            if await item.is_visible(timeout=300):
                await item.click()
                return True
        except Exception:
            continue
    return False


async def choose_select_option(container: Locator, value: str) -> bool:
    selects = container.locator("select")
    count = await selects.count()
    for i in range(count):
        select = selects.nth(i)
        try:
            if not await select.is_visible(timeout=300):
                continue
            await select.select_option(label=str(value))
            return True
        except Exception:
            try:
                await select.select_option(value=str(value))
                return True
            except Exception:
                continue
    return False


async def fill_container_answer(container: Locator, answer_type: str, value: Any, label: str) -> FillResult:
    if answer_type == "file":
        file_path = Path(str(value))
        if not str(value).strip():
            return FillResult(label, False, "file path is empty")
        if not file_path.exists() or not file_path.is_file():
            return FillResult(label, False, f"file not found: {file_path}")
        file_inputs = container.locator("input[type='file']")
        count = await file_inputs.count()
        for i in range(count):
            field = file_inputs.nth(i)
            try:
                await field.set_input_files(str(file_path))
                return FillResult(label, True, "uploaded file")
            except Exception:
                continue
        return FillResult(label, False, "file input not found")

    if answer_type == "text":
        ok = await fill_text_in_container(container, str(value))
        return FillResult(label, ok, "filled text" if ok else "text input not found")

    if answer_type in {"single", "radio"}:
        if await choose_select_option(container, str(value)):
            return FillResult(label, True, "selected dropdown option")
        ok = await click_option(container, str(value))
        if ok:
            return FillResult(label, True, "clicked single option")
        text_ok = await fill_text_in_container(container, str(value))
        return FillResult(label, text_ok, "filled text fallback" if text_ok else "option not found")

    if answer_type in {"multiple", "checkbox"}:
        values = value if isinstance(value, list) else [value]
        missed: list[str] = []
        for item in values:
            ok = await click_option(container, str(item))
            if not ok:
                missed.append(str(item))
        if missed:
            return FillResult(label, False, f"missing options: {', '.join(missed)}")
        return FillResult(label, True, "clicked multiple options")

    return FillResult(label, False, f"unsupported type: {answer_type}")


async def fill_answer(page: Page, keywords: list[str], answer_type: str, value: Any, label: str | None = None) -> FillResult:
    label = label or " / ".join(keywords) or "<answer>"
    container = await find_question_container(page, keywords)
    if container is None:
        return FillResult(label, False, "question container not found")

    return await fill_container_answer(container, answer_type, value, label)


def apply_required(result: FillResult, required: bool) -> FillResult:
    result.blocking = required
    if not required and not result.ok:
        result.detail = f"optional skipped: {result.detail}"
    return result


async def container_has_controls(container: Locator) -> bool:
    controls = container.locator("input, textarea, select, [role='radio'], [role='checkbox'], button")
    return await controls.count() > 0


async def container_is_answered(container: Locator) -> bool:
    text_inputs = container.locator(TEXT_INPUTS)
    for i in range(await text_inputs.count()):
        field = text_inputs.nth(i)
        try:
            if await field.is_visible(timeout=200) and await field.input_value(timeout=200):
                return True
        except Exception:
            continue

    checked = container.locator("input[type='radio']:checked, input[type='checkbox']:checked, [aria-checked='true']")
    try:
        return await checked.count() > 0
    except Exception:
        return False


async def extract_question_options(container: Locator) -> list[str]:
    try:
        raw_options = await container.evaluate(
            """element => {
                const values = [];
                const add = value => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    if (text && !values.includes(text)) values.push(text);
                };

                element.querySelectorAll('select option').forEach(option => {
                    if (!option.disabled) add(option.textContent || option.value);
                });

                element.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(input => {
                    if (input.id) {
                        const label = element.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                        if (label) add(label.textContent);
                    }
                    const parentLabel = input.closest('label');
                    if (parentLabel) add(parentLabel.textContent);
                    const wrapper = input.closest('li, .ui-radio, .ui-checkbox, .ant-radio-wrapper, .ant-checkbox-wrapper, .el-radio, .el-checkbox');
                    if (wrapper) add(wrapper.textContent);
                    if (input.value && !['on', ''].includes(input.value)) add(input.value);
                });

                element.querySelectorAll('[role="radio"], [role="checkbox"]').forEach(item => add(item.textContent || item.getAttribute('aria-label')));
                return values;
            }"""
        )
    except Exception:
        return []

    options: list[str] = []
    seen: set[str] = set()
    for item in raw_options:
        text = re.sub(r"\s+", " ", str(item)).strip()
        key = normalize(text)
        if text and key and key not in seen:
            seen.add(key)
            options.append(text)
    return options


async def infer_answer_type(container: Locator) -> str:
    try:
        if await container.locator("input[type='checkbox'], [role='checkbox']").count():
            return "multiple"
        if await container.locator("input[type='radio'], [role='radio']").count():
            return "single"
        select_count = await container.locator("select").count()
        if select_count:
            for i in range(select_count):
                select = container.locator("select").nth(i)
                try:
                    if await select.evaluate("element => Boolean(element.multiple)"):
                        return "multiple"
                except Exception:
                    continue
            return "single"
        if await container.locator(TEXT_INPUTS).count():
            return "text"
    except Exception:
        pass
    return "text"


def looks_like_validation_question(text: str, options: list[str], llm_config: dict[str, Any]) -> bool:
    normalized_text = normalize(text)
    if not normalized_text:
        return False

    keywords = llm_config.get("trigger_keywords", DEFAULT_LLM_VALIDATION_KEYWORDS)
    if isinstance(keywords, str):
        keywords = [keywords]
    if any(normalize(str(keyword)) in normalized_text for keyword in keywords):
        return True

    arithmetic_patterns = [
        r"\d+\s*[+\-*/x×÷]\s*\d+",
        r"\d+\s*(加|减|乘|除以|除)\s*\d+",
        r"(等于|结果|答案|多少)",
    ]
    if any(re.search(pattern, text, re.I) for pattern in arithmetic_patterns):
        return True

    option_text = normalize(" ".join(options))
    return bool(option_text and any(keyword in option_text for keyword in ["选a", "选b", "选c", "第二项", "全选"]))


def llm_should_handle_question(text: str, options: list[str], llm_config: dict[str, Any]) -> bool:
    mode = str(llm_config.get("mode", "validation_only")).strip().lower()
    if mode in {"all_unanswered", "all", "fallback"}:
        return True
    return looks_like_validation_question(text, options, llm_config)


def question_contains_private_info(text: str, llm_config: dict[str, Any]) -> bool:
    keywords = llm_config.get("privacy_keywords", DEFAULT_PRIVACY_KEYWORDS)
    if isinstance(keywords, str):
        keywords = [keywords]
    normalized_text = normalize(text)
    return any(normalize(str(keyword)) in normalized_text for keyword in keywords)


def safe_question_text_for_llm(text: str) -> str:
    patterns = [
        r"1[3-9]\d{9}",
        r"\b\d{6,}\b",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    ]
    sanitized = text
    for pattern in patterns:
        sanitized = re.sub(pattern, "[redacted]", sanitized)
    return sanitized


def value_summary_for_llm(value: Any, share_values: bool) -> Any:
    if share_values:
        return value
    if isinstance(value, list):
        return f"[local value hidden; {len(value)} item(s)]"
    if value is None or str(value).strip() == "":
        return "[empty]"
    return "[local value hidden]"


def add_local_answer(
    items: list[LocalAnswer],
    label: str,
    answer_type: str,
    value: Any,
    keywords: list[str] | None = None,
    source: str = "config",
) -> None:
    if value is None or value == [] or str(value).strip() == "":
        return
    clean_label = str(label).strip() or f"local answer {len(items) + 1}"
    clean_type = str(answer_type or "text").strip().lower()
    if clean_type == "radio":
        clean_type = "single"
    if clean_type == "checkbox":
        clean_type = "multiple"
    if clean_type not in {"text", "single", "multiple"}:
        clean_type = "text"
    clean_keywords = [str(item).strip() for item in (keywords or []) if str(item).strip()]
    items.append(
        LocalAnswer(
            local_id=f"local_{len(items) + 1}",
            label=clean_label,
            answer_type=clean_type,
            value=value,
            keywords=clean_keywords,
            source=source,
        )
    )


def build_local_answer_catalog(config: dict[str, Any]) -> list[LocalAnswer]:
    items: list[LocalAnswer] = []
    for label, value in dict(config.get("profile", {})).items():
        add_local_answer(items, str(label), "text", value, [str(label)], "profile")

    for field in config.get("profile_fields", []):
        if not isinstance(field, dict):
            continue
        keywords = answer_keywords(field)
        label = str(field.get("label") or " / ".join(keywords) or "<profile field>")
        add_local_answer(items, label, "text", field.get("value", ""), keywords, "profile_fields")

    for answer in config.get("answers", []):
        if not isinstance(answer, dict):
            continue
        keywords = answer_keywords(answer)
        label = str(answer.get("label") or " / ".join(keywords) or "<answer>")
        add_local_answer(items, label, str(answer.get("type", "text")), answer.get("value", ""), keywords, "answers")

    for rule in config.get("simple_validation_rules", []):
        if not isinstance(rule, dict):
            continue
        keywords = answer_keywords(rule)
        label = str(rule.get("label") or " / ".join(keywords) or "<validation rule>")
        add_local_answer(items, label, str(rule.get("type", "single")), rule.get("value", ""), keywords, "simple_validation_rules")

    ml_config = config.get("ml_classifier")
    if isinstance(ml_config, dict):
        label_answers = ml_config.get("label_answers")
        if isinstance(label_answers, dict):
            for label, answer in label_answers.items():
                if not isinstance(answer, dict):
                    continue
                add_local_answer(
                    items,
                    str(label),
                    str(answer.get("type", "text")),
                    answer.get("value", ""),
                    [str(label)],
                    "ml_classifier.label_answers",
                )
    return items


def local_catalog_for_llm(items: list[LocalAnswer], share_values: bool) -> list[dict[str, Any]]:
    return [
        {
            "local_id": item.local_id,
            "label": item.label,
            "answer_type": item.answer_type,
            "keywords": item.keywords,
            "source": item.source,
            "value": value_summary_for_llm(item.value, share_values),
        }
        for item in items
    ]


def first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_llm_answer(raw: str, inferred_answer_type: str = "text", options: list[str] | None = None) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        json_text = first_json_object(text)
        if not json_text:
            compact = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’。！!]+$", "", text)
            if not compact or len(compact) > 30:
                return None
            if options and not option_matches(compact, options, inferred_answer_type):
                return None
            return {
                "answer_type": inferred_answer_type if inferred_answer_type in {"single", "multiple", "text"} else "text",
                "value": compact,
                "confidence": 0.75,
                "reason": "plain text fallback",
            }
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    confidence_present = "confidence" in data
    answer_type = str(data.get("answer_type", "skip")).strip().lower()
    if answer_type not in LLM_ANSWER_TYPES:
        answer_type = "skip"
    data["answer_type"] = answer_type
    data["_confidence_present"] = confidence_present
    try:
        data["confidence"] = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    return data


def parse_llm_route_answer(raw: str, inferred_answer_type: str = "text", options: list[str] | None = None) -> dict[str, Any] | None:
    data = parse_llm_answer(raw, inferred_answer_type, options)
    if data is None:
        return None

    decision = str(data.get("decision", "")).strip().lower()
    answer_type = str(data.get("answer_type", "skip")).strip().lower()
    if not decision:
        decision = "skip" if answer_type == "skip" else "llm_answer"
    if decision not in LLM_ROUTE_DECISIONS:
        decision = "skip"
    data["decision"] = decision

    local_id = str(data.get("local_id", "")).strip()
    data["local_id"] = local_id
    if decision == "local" and local_id and not data.get("_confidence_present"):
        data["confidence"] = 0.75
    return data


def call_openai_compatible_chat_sync(llm_config: dict[str, Any], messages: list[dict[str, str]]) -> str:
    api_key_env = str(llm_config.get("api_key_env", "OPENAI_API_KEY"))
    if api_key_env.lower().startswith("sk-"):
        raise RuntimeError("api_key_env should be an environment variable name, not the API key itself")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"missing API key env var: {api_key_env}")

    base_url = str(llm_config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": str(llm_config.get("model", "gpt-4o-mini")),
        "messages": messages,
        "temperature": float(llm_config.get("temperature", 0)),
    }
    if bool(llm_config.get("json_mode", True)):
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = float(llm_config.get("timeout_seconds", 20))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

    data = json.loads(response_body)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response content is empty")
    return content


async def call_openai_compatible_chat(llm_config: dict[str, Any], messages: list[dict[str, str]]) -> str:
    return await asyncio.to_thread(call_openai_compatible_chat_sync, llm_config, messages)


def option_matches(value: Any, options: list[str], answer_type: str) -> bool:
    if answer_type in {"text", "skip"} or not options:
        return True
    allowed = {normalize(option) for option in options}
    values = value if isinstance(value, list) else [value]
    return all(normalize(str(item)) in allowed for item in values)


async def fill_llm_validation_questions(page: Page, config: dict[str, Any]) -> list[FillResult]:
    llm_config = dict(config.get("llm_validation", {}))
    if not llm_config.get("enabled", False):
        return []

    threshold = float(llm_config.get("confidence_threshold", 0.7))
    max_text_length = int(llm_config.get("max_text_length", 500))
    max_candidates = int(llm_config.get("max_candidates", 20))
    local_routing_enabled = bool(llm_config.get("local_routing", True))
    share_local_values = bool(llm_config.get("share_local_values", False))
    local_answers = build_local_answer_catalog(config) if local_routing_enabled else []
    local_answer_by_id = {item.local_id: item for item in local_answers}
    local_catalog = local_catalog_for_llm(local_answers, share_local_values)
    results: list[FillResult] = []
    seen_text: set[str] = set()
    candidates = page.locator(ML_QUESTION_CONTAINERS)
    count = await candidates.count()

    for i in range(min(count, max_candidates)):
        container = candidates.nth(i)
        try:
            if not await container.is_visible(timeout=200):
                continue
            if not await container_has_controls(container):
                continue
            if await container_is_answered(container):
                continue
            text = (await container.inner_text(timeout=500)).strip()
        except Exception:
            continue

        signature = normalize(text)
        if not signature or signature in seen_text or len(signature) > max_text_length:
            continue
        seen_text.add(signature)

        answer_type = await infer_answer_type(container)
        options = await extract_question_options(container)
        result_label = f"llm:{text[:40]}"
        if not llm_should_handle_question(text, options, llm_config):
            continue

        system_prompt = (
            "你是问卷自动填写助手的路由和兜底回答模块，负责处理本地规则没有填上的文字题和选择题。"
            "你必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释、前后缀文字。"
            "先判断题目是否是在询问 local_info_catalog 中已有的本地信息。"
            "如果是，返回 decision=local，并填写最匹配的 local_id；不要把本地值写进 value。"
            "如果不是本地信息，才根据题目和选项给出一个简短、自然、可直接填写的答案，并返回 decision=llm_answer。"
            "不要索取、推断或生成个人隐私信息。遇到姓名、学号、手机号、微信、身份证、邮箱、地址、班级、学院等隐私相关题，如果 local_info_catalog 没有对应项，返回 decision=skip。"
            "遇到图片验证码、滑块、人机验证、短信验证、二维码验证或需要外部实时信息的问题，返回 answer_type=skip。"
            "如果有选项，value 必须完全使用给定选项中的文本。只返回 JSON。"
            "填空题的 value 只能是最终答案，尽量不超过 20 个字，不要解释、不要复杂、不要学术化。"
            "不确定时降低 confidence；无法可靠回答时返回 answer_type=skip。"
        )
        user_payload = {
            "question_text": safe_question_text_for_llm(text),
            "inferred_answer_type": answer_type,
            "options": options,
            "local_info_catalog": local_catalog,
            "required_schema": {
                "decision": "local | llm_answer | skip",
                "local_id": "when decision=local, choose one local_id from local_info_catalog",
                "answer_type": "text | single | multiple | skip",
                "value": "empty for local/skip; string for text/single, array for multiple",
                "confidence": "0 to 1",
                "reason": "short Chinese reason",
            },
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

        try:
            raw_answer = await call_openai_compatible_chat(llm_config, messages)
        except Exception as exc:
            results.append(FillResult(result_label, False, str(exc)))
            continue

        parsed = parse_llm_route_answer(raw_answer, answer_type, options)
        if parsed is None:
            results.append(FillResult(result_label, False, f"could not parse LLM JSON; raw={raw_answer[:120]!r}"))
            continue

        decision = str(parsed.get("decision", "skip"))
        llm_answer_type = str(parsed.get("answer_type", "skip"))
        confidence = float(parsed.get("confidence", 0))
        value = parsed.get("value", "")
        reason = str(parsed.get("reason", "")).strip()
        local_id = str(parsed.get("local_id", "")).strip()
        if llm_answer_type == "radio":
            llm_answer_type = "single"
        if llm_answer_type == "checkbox":
            llm_answer_type = "multiple"
        if llm_answer_type == "multiple" and isinstance(value, str):
            value = [item.strip() for item in re.split(r"[,，、;；]", value) if item.strip()]
        if confidence < threshold:
            results.append(FillResult(result_label, True, f"skipped below confidence threshold ({confidence:.2f})"))
            continue
        if decision == "local":
            local_answer = local_answer_by_id.get(local_id)
            if local_answer is None:
                results.append(FillResult(result_label, False, f"LLM selected unknown local_id: {local_id or '<empty>'}"))
                continue
            result = await fill_container_answer(
                container,
                local_answer.answer_type,
                local_answer.value,
                f"local:{local_answer.label}",
            )
            result.detail = f"{result.detail}; routed by LLM to local info '{local_answer.label}'"
            if reason:
                result.detail = f"{result.detail}; {reason}"
            results.append(result)
            continue
        if decision == "skip" or llm_answer_type == "skip":
            results.append(FillResult(result_label, True, f"LLM skipped: {reason or 'not a validation question'}"))
            continue
        if question_contains_private_info(text, llm_config):
            results.append(FillResult(result_label, False, "privacy-related question left for manual handling"))
            continue
        if not option_matches(value, options, llm_answer_type):
            results.append(FillResult(result_label, False, f"LLM answer not in options: {value}"))
            continue

        result = await fill_container_answer(container, llm_answer_type, value, result_label)
        if result.ok and reason:
            result.detail = f"{result.detail}; {reason}"
        results.append(result)

    return results


async def fill_ml_classified_questions(page: Page, config: dict[str, Any], config_path: Path) -> list[FillResult]:
    ml_config = dict(config.get("ml_classifier", {}))
    if not ml_config.get("enabled", False):
        return []

    model_path = Path(ml_config.get("model_path", "question_model.json"))
    if not model_path.is_absolute():
        model_path = config_path.parent / model_path
    if not model_path.exists():
        return [FillResult("ml_classifier", False, f"model file not found: {model_path}")]

    label_answers = dict(ml_config.get("label_answers", {}))
    threshold = float(ml_config.get("confidence_threshold", 0.75))
    model = QuestionClassifier.load(model_path)

    results: list[FillResult] = []
    seen_text: set[str] = set()
    candidates = page.locator(ML_QUESTION_CONTAINERS)
    count = await candidates.count()
    for i in range(min(count, int(ml_config.get("max_candidates", 120)))):
        container = candidates.nth(i)
        try:
            if not await container.is_visible(timeout=200):
                continue
            if not await container_has_controls(container):
                continue
            if await container_is_answered(container):
                continue
            text = (await container.inner_text(timeout=500)).strip()
        except Exception:
            continue

        signature = normalize(text)
        if not signature or signature in seen_text or len(signature) > int(ml_config.get("max_text_length", 300)):
            continue
        seen_text.add(signature)

        label, confidence = model.predict(text)
        answer = label_answers.get(label)
        result_label = f"ml:{label} ({confidence:.2f})"
        if confidence < threshold:
            results.append(FillResult(result_label, True, "skipped below confidence threshold"))
            continue
        if not isinstance(answer, dict):
            results.append(FillResult(result_label, False, "label has no configured answer"))
            continue

        result = await fill_container_answer(
            container,
            str(answer.get("type", "text")),
            answer.get("value", ""),
            result_label,
        )
        results.append(result)

    return results


async def fill_profile(page: Page, profile: dict[str, Any]) -> list[FillResult]:
    results: list[FillResult] = []
    for label, value in profile.items():
        keywords = [str(label)]
        result = await fill_answer(page, keywords, "text", value, str(label))
        if not result.ok:
            result = await fill_text_by_label_or_placeholder(page, str(label), str(value))
        results.append(apply_required(result, False))
    return results


async def fill_profile_fields(page: Page, fields: list[dict[str, Any]]) -> list[FillResult]:
    results: list[FillResult] = []
    for field in fields:
        keywords = answer_keywords(field)
        label = str(field.get("label") or " / ".join(keywords) or "<profile field>")
        if not keywords:
            results.append(FillResult(label, False, "missing keywords"))
            continue
        value = field.get("value", "")
        required = bool(field.get("required", False))
        result = await fill_answer(page, keywords, "text", value, label)
        if not result.ok:
            for keyword in keywords:
                result = await fill_text_by_label_or_placeholder(page, keyword, str(value))
                if result.ok:
                    result.label = label
                    break
        results.append(apply_required(result, required))
    return results


async def fill_text_by_label_or_placeholder(page: Page, label: str, value: str) -> FillResult:
    try:
        field = page.get_by_label(label)
        if await field.count():
            await field.first.fill(value)
            return FillResult(label, True, "filled by label")
    except Exception:
        pass

    try:
        field = page.get_by_placeholder(label, exact=False)
        count = await field.count()
        for i in range(count):
            candidate = field.nth(i)
            if await candidate.is_visible(timeout=300) and await candidate.is_enabled(timeout=300):
                await candidate.fill(value)
                return FillResult(label, True, "filled by placeholder")
    except Exception:
        pass

    return FillResult(label, False, "text field not found")


async def fill_simple_validation_rules(page: Page, rules: list[dict[str, Any]]) -> list[FillResult]:
    results: list[FillResult] = []
    for rule in rules:
        keywords = answer_keywords(rule)
        label = str(rule.get("label") or " / ".join(keywords) or "<validation rule>")
        if not keywords:
            results.append(FillResult(label, False, "missing keywords"))
            continue
        result = await fill_answer(
                page,
                keywords,
                str(rule.get("type", "single")),
                rule.get("value"),
                label,
            )
        results.append(apply_required(result, bool(rule.get("required", False))))
    return results


async def fill_terms_agreements(page: Page, config: dict[str, Any]) -> list[FillResult]:
    if not bool(config.get("auto_agree_terms", True)):
        return []

    keywords = config.get("terms_agreement_keywords", DEFAULT_TERMS_AGREEMENT_KEYWORDS)
    negative_keywords = config.get("terms_agreement_negative_keywords", DEFAULT_TERMS_AGREEMENT_NEGATIVE_KEYWORDS)
    if isinstance(keywords, str):
        keywords = [keywords]
    if isinstance(negative_keywords, str):
        negative_keywords = [negative_keywords]

    normalized_keywords = [normalize(str(item)) for item in keywords if str(item).strip()]
    normalized_negative = [normalize(str(item)) for item in negative_keywords if str(item).strip()]
    if not normalized_keywords:
        return []

    results: list[FillResult] = []
    candidates = page.locator("input[type='checkbox'], [role='checkbox']")
    count = await candidates.count()
    for i in range(min(count, 50)):
        checkbox = candidates.nth(i)
        try:
            if not await checkbox.is_visible(timeout=200):
                continue
            if not await checkbox.is_enabled(timeout=200):
                continue
            is_checked = await checkbox.is_checked(timeout=200)
        except Exception:
            try:
                is_checked = (await checkbox.get_attribute("aria-checked")) == "true"
            except Exception:
                continue
        if is_checked:
            continue

        try:
            label_text = await checkbox.evaluate(
                """element => {
                    const texts = [];
                    const add = value => {
                        const text = String(value || '').replace(/\\s+/g, ' ').trim();
                        if (text) texts.push(text);
                    };
                    if (element.id) {
                        const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
                        if (label) add(label.textContent);
                    }
                    add(element.getAttribute('aria-label'));
                    add(element.closest('label')?.textContent);
                    add(element.closest('.agreement, .agree, .protocol, .privacy, .wjagree, .layui-form-checkbox, .ant-checkbox-wrapper, .el-checkbox, li, p, div')?.textContent);
                    return texts.join(' ');
                }"""
            )
        except Exception:
            continue

        normalized_text = normalize(str(label_text))
        if not normalized_text:
            continue
        if any(item in normalized_text for item in normalized_negative):
            continue
        if not any(item in normalized_text for item in normalized_keywords):
            continue

        try:
            await checkbox.click()
            results.append(FillResult("terms agreement", True, f"checked agreement: {str(label_text).strip()[:60]}"))
        except Exception as exc:
            results.append(FillResult("terms agreement", False, f"agreement checkbox click failed: {exc}"))

    return results


async def click_terms_agreement_buttons(page: Page, config: dict[str, Any]) -> list[FillResult]:
    if not bool(config.get("auto_agree_terms", True)):
        return []

    button_texts = config.get("terms_agreement_button_texts", DEFAULT_TERMS_AGREEMENT_BUTTON_TEXTS)
    negative_keywords = config.get("terms_agreement_negative_keywords", DEFAULT_TERMS_AGREEMENT_NEGATIVE_KEYWORDS)
    if isinstance(button_texts, str):
        button_texts = [button_texts]
    if isinstance(negative_keywords, str):
        negative_keywords = [negative_keywords]

    normalized_negative = [normalize(str(item)) for item in negative_keywords if str(item).strip()]
    results: list[FillResult] = []
    for text in [str(item) for item in button_texts if str(item).strip()]:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
            page.locator("button, input[type='button'], input[type='submit'], a").filter(has_text=re.compile(re.escape(text), re.I)),
            page.get_by_text(re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)),
        ]
        for locator in candidates:
            count = await locator.count()
            for i in range(min(count, 10)):
                button = locator.nth(i)
                try:
                    if not await button.is_visible(timeout=300):
                        continue
                    label = (await button.inner_text(timeout=300)).strip()
                    if any(item in normalize(label or text) for item in normalized_negative):
                        continue
                    await button.click()
                    results.append(FillResult("terms agreement button", True, f"clicked: {label or text}"))
                    return results
                except Exception:
                    continue
    return results


async def submit_form(page: Page, button_texts: list[str]) -> bool:
    for text in button_texts:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
            page.locator("button, input[type='submit'], input[type='button']").filter(has_text=re.compile(re.escape(text), re.I)),
            page.get_by_text(re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)),
        ]
        for locator in candidates:
            count = await locator.count()
            for i in range(min(count, 10)):
                button = locator.nth(i)
                try:
                    if await button.is_visible(timeout=300) and await button.is_enabled(timeout=300):
                        await button.click()
                        return True
                except Exception:
                    continue
    return False


async def detect_submit_errors(page: Page) -> list[str]:
    found: list[str] = []
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body = ""

    for text in SUBMIT_ERROR_TEXT:
        if text in body:
            found.append(text)
    for text in SUBMIT_PROBLEM_TEXT:
        if text in title or text in body:
            found.append(text)
    return found


def qr_code_keywords(config: dict[str, Any]) -> list[str]:
    raw = config.get("qr_code_keywords", DEFAULT_QR_CODE_KEYWORDS)
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def safe_filename_part(value: str, fallback: str, max_length: int = 48) -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value or "").strip(" ._")
    text = re.sub(r"\s+", "_", text)
    if not text:
        text = fallback
    return text[:max_length].strip(" ._") or fallback


def resolve_qr_code_output_dir(config: dict[str, Any], config_path: Path) -> Path:
    raw = str(config.get("qr_code_output_dir", "saved_qr_codes")).strip() or "saved_qr_codes"
    output_dir = Path(raw)
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many duplicate files for {path}")


def qr_code_image_path(config: dict[str, Any], config_path: Path, index: int) -> Path:
    _, tz = config_timezone(config)
    timestamp = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    label = safe_filename_part(str(config.get("label") or f"questionnaire_{index}"), f"questionnaire_{index}")
    url_hash = hashlib.sha1(str(config.get("url", "")).encode("utf-8")).hexdigest()[:10]
    output_dir = resolve_qr_code_output_dir(config, config_path)
    return unique_path(output_dir / f"{index:02d}_{label}_{timestamp}_{url_hash}_qr.png")


def append_qr_code_record(
    config: dict[str, Any],
    config_path: Path,
    image_path: Path,
    page: Page,
    detail: str,
) -> None:
    output_dir = resolve_qr_code_output_dir(config, config_path)
    _, tz = config_timezone(config)
    record = {
        "saved_at": datetime.now(tz).isoformat(),
        "label": str(config.get("label", "")),
        "questionnaire_url": str(config.get("url", "")),
        "result_page_url": page.url,
        "image_path": str(image_path),
        "detail": detail,
    }
    record_path = output_dir / "qr_code_records.jsonl"
    with record_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_qr_code_index(output_dir)


def load_qr_code_records(output_dir: Path) -> list[dict[str, Any]]:
    record_path = output_dir / "qr_code_records.jsonl"
    if not record_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with record_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records


def write_qr_code_index(output_dir: Path) -> None:
    cards: list[str] = []
    for record in reversed(load_qr_code_records(output_dir)):
        image_path = Path(str(record.get("image_path", "")))
        image_src = html_lib.escape(image_path.name)
        image_exists = (output_dir / image_path.name).exists()
        label = html_lib.escape(str(record.get("label") or "未命名问卷"))
        saved_at = html_lib.escape(str(record.get("saved_at") or ""))
        questionnaire_url = html_lib.escape(str(record.get("questionnaire_url") or ""))
        result_url = html_lib.escape(str(record.get("result_page_url") or ""))
        detail = html_lib.escape(str(record.get("detail") or ""))
        image_html = (
            f'<img src="{image_src}" alt="{label} 二维码">'
            if image_exists
            else '<div class="missing">图片文件不存在</div>'
        )
        cards.append(
            f"""
            <article class="card">
              {image_html}
              <div class="meta">
                <h2>{label}</h2>
                <p><strong>保存时间：</strong>{saved_at}</p>
                <p><strong>识别信息：</strong>{detail}</p>
                <p><strong>问卷链接：</strong><a href="{questionnaire_url}">{questionnaire_url}</a></p>
                <p><strong>结果页：</strong><a href="{result_url}">{result_url}</a></p>
              </div>
            </article>
            """
        )

    body = "\n".join(cards) if cards else "<p>还没有保存二维码。</p>"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>问卷二维码记录</title>
  <style>
    body {{ margin: 0; font-family: "Microsoft YaHei", Arial, sans-serif; background: #f6f7f9; color: #1f2937; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 48px; }}
    h1 {{ margin: 0 0 18px; font-size: 24px; }}
    .card {{ display: flex; gap: 18px; align-items: flex-start; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin: 14px 0; }}
    img, .missing {{ width: 180px; height: 180px; flex: 0 0 auto; border: 1px solid #d1d5db; border-radius: 6px; background: #fff; object-fit: contain; }}
    .missing {{ display: grid; place-items: center; color: #9ca3af; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ margin: 6px 0; line-height: 1.55; overflow-wrap: anywhere; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <main>
    <h1>问卷二维码记录</h1>
    {body}
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


async def qr_candidate_score(locator: Locator, keywords: list[str]) -> tuple[float, str] | None:
    try:
        if not await locator.is_visible(timeout=300):
            return None
        box = await locator.bounding_box(timeout=500)
    except Exception:
        return None
    if not box:
        return None

    width = float(box.get("width") or 0)
    height = float(box.get("height") or 0)
    if width < 60 or height < 60:
        return None

    ratio = width / height if height else 0
    if ratio < 0.55 or ratio > 1.8:
        return None

    try:
        info = await locator.evaluate(
            """element => {
                const values = [];
                const add = value => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    if (text) values.push(text);
                };
                add(element.tagName);
                add(element.getAttribute('alt'));
                add(element.getAttribute('title'));
                add(element.getAttribute('aria-label'));
                add(element.getAttribute('src'));
                add(element.getAttribute('href'));
                add(element.getAttribute('style'));
                let current = element;
                for (let depth = 0; current && depth < 4; depth += 1) {
                    add(current.innerText);
                    current = current.parentElement;
                }
                return values.join(' ');
            }"""
        )
    except Exception:
        info = ""

    info_text = str(info)
    normalized_info = normalize(info_text)
    score = 0.0
    if 0.75 <= ratio <= 1.35:
        score += 20
    if 0.9 <= ratio <= 1.1:
        score += 15
    if min(width, height) >= 100:
        score += 10
    if min(width, height) >= 160:
        score += 10

    hits = [keyword for keyword in keywords if normalize(keyword) and normalize(keyword) in normalized_info]
    if hits:
        score += 60
    if any(item in normalized_info for item in ["qrcode", "qr-code", "qr_code"]):
        score += 40
    if "canvas" in normalized_info or "svg" in normalized_info:
        score += 5

    if score < 35 and not (0.85 <= ratio <= 1.15 and min(width, height) >= 140):
        return None

    detail = f"{int(width)}x{int(height)}"
    if hits:
        detail += f"; keywords={', '.join(hits[:3])}"
    return score, detail


async def find_qr_code_candidate(page: Page, config: dict[str, Any]) -> tuple[Locator, str] | None:
    keywords = qr_code_keywords(config)
    candidates = page.locator(QR_CODE_SELECTORS)
    count = await candidates.count()
    best: tuple[float, int, str] | None = None
    for i in range(min(count, int(config.get("qr_code_max_candidates", 80)))):
        locator = candidates.nth(i)
        scored = await qr_candidate_score(locator, keywords)
        if scored is None:
            continue
        score, detail = scored
        if best is None or score > best[0]:
            best = (score, i, detail)
    if best is None:
        return None
    _, index, detail = best
    return candidates.nth(index), detail


async def save_qr_code_image(page: Page, config: dict[str, Any], config_path: Path, index: int) -> Path | None:
    if not bool(config.get("save_qr_code_images", True)):
        return None

    wait_seconds = nonnegative_seconds(config, ["qr_code_wait_seconds"], 5.0)
    deadline = time.monotonic() + wait_seconds
    candidate: tuple[Locator, str] | None = None
    while True:
        candidate = await find_qr_code_candidate(page, config)
        if candidate is not None or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    if candidate is None:
        print(f"[qr] no QR code image found after submit within {format_seconds(wait_seconds)}s")
        return None

    locator, detail = candidate
    image_path = qr_code_image_path(config, config_path, index)
    await locator.screenshot(path=str(image_path))
    append_qr_code_record(config, config_path, image_path, page, detail)
    print(f"[qr] saved QR code image: {image_path}")
    return image_path


async def wait_for_manual_review(page: Page, config: dict[str, Any]) -> None:
    seconds = int(config.get("manual_review_seconds", 3600))
    if seconds <= 0:
        return
    print(f"[manual] keeping browser open for {seconds}s; press Ctrl+C in PowerShell to stop earlier")
    await page.wait_for_timeout(seconds * 1000)


def print_results(title: str, results: list[FillResult], continue_on_error: bool = False) -> bool:
    print(f"\n[{title}]")
    all_ok = True
    for result in results:
        status = "OK" if result.ok else ("MISS" if result.blocking else "SKIP")
        print(f"  {status} {result.label}: {result.detail}")
        all_ok = all_ok and (result.ok or not result.blocking)
    if continue_on_error and not all_ok:
        print(f"  [continue] {title} has unfinished items; continuing because continue_on_fill_error=true")
        return True
    return all_ok


async def process_questionnaire(page: Page, config: dict[str, Any], config_path: Path, index: int, total: int) -> int:
    label = str(config.get("label") or f"questionnaire #{index}")
    await wait_until_start(config)
    questionnaire_started_at = time.monotonic()

    print(f"\n[questionnaire {index}/{total}] {label}")
    print(f"[open] {config['url']}")
    await page.goto(config["url"], wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle", timeout=15000)

    wechat_only = await detect_wechat_only_page(page)
    if wechat_only:
        print("\n[stop] this form can only be filled in WeChat:")
        for reason in wechat_only:
            print(f"  - {reason}")
        print("[manual] desktop browser cannot complete this form or capture the after-submit group QR")
        await wait_for_manual_review(page, config)
        return 7

    complex_before = await detect_complex_verification(page)
    if complex_before:
        print("\n[stop] complex verification detected before filling:")
        for reason in complex_before:
            print(f"  - {reason}")
        print("[manual] browser is left open for manual handling")
        await wait_for_manual_review(page, config)
        return 2

    profile_results = await fill_profile(page, dict(config.get("profile", {})))
    profile_results.extend(await fill_profile_fields(page, list(config.get("profile_fields", []))))
    answer_results = []
    for answer in config.get("answers", []):
        keywords = answer_keywords(answer)
        answer_label = str(answer.get("label") or " / ".join(keywords) or "<answer>")
        if not keywords:
            answer_results.append(FillResult(answer_label, False, "missing keywords"))
            continue
        result = await fill_answer(
                page,
                keywords,
                str(answer.get("type", "text")),
                answer.get("value", ""),
                answer_label,
            )
        answer_results.append(apply_required(result, bool(answer.get("required", True))))
    ml_results = await fill_ml_classified_questions(page, config, config_path)
    validation_results = await fill_simple_validation_rules(page, list(config.get("simple_validation_rules", [])))
    terms_results = await fill_terms_agreements(page, config)
    llm_results = await fill_llm_validation_questions(page, config)

    continue_on_error = bool(config.get("continue_on_fill_error", True))
    profile_ok = print_results("profile", profile_results, continue_on_error)
    answers_ok = print_results("answers", answer_results, continue_on_error)
    ml_ok = print_results("ml classifier", ml_results, continue_on_error)
    validation_ok = print_results("simple validation", validation_results, continue_on_error)
    terms_ok = print_results("terms agreement", terms_results, False)
    llm_ok = print_results("llm validation", llm_results, False)

    complex_after = await detect_complex_verification(page)
    if complex_after:
        print("\n[stop] complex verification detected after filling:")
        for reason in complex_after:
            print(f"  - {reason}")
        print("[manual] browser is left open for manual handling")
        await wait_for_manual_review(page, config)
        return 2

    if not (profile_ok and answers_ok and ml_ok and validation_ok and terms_ok and llm_ok):
        print("\n[stop] not all configured fields were filled; review manually")
        await wait_for_manual_review(page, config)
        return 3

    if not bool(config.get("auto_submit", True)):
        print("\n[manual] auto_submit=false; review and submit manually")
        await wait_for_manual_review(page, config)
        return 0

    delay = nonnegative_seconds(config, ["submit_delay_seconds"], 3.0)
    if delay:
        print(f"\n[submit] waiting {format_seconds(delay)}s before submit")
        await asyncio.sleep(delay)

    await wait_min_submit_after_start(config, questionnaire_started_at)
    await wait_until_submit_time(config)

    submitted = await submit_form(page, list(config.get("submit_button_texts", ["提交", "Submit"])))
    if not submitted:
        print("[stop] submit button not found; browser is left open")
        await wait_for_manual_review(page, config)
        return 4

    print("[submit] clicked submit button")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    await page.wait_for_timeout(800)
    confirmation_texts = list(config.get("submit_confirmation_button_texts", ["确认提交", "确定", "确认"]))
    confirmation_terms_results = await fill_terms_agreements(page, config)
    agreement_button_results = await click_terms_agreement_buttons(page, config)
    confirmation_terms_ok = print_results("terms agreement after submit", confirmation_terms_results + agreement_button_results, False)
    if not confirmation_terms_ok:
        print("[stop] terms agreement could not be accepted after submit")
        await wait_for_manual_review(page, config)
        return 6
    if confirmation_texts and await submit_form(page, confirmation_texts):
        print("[submit] clicked confirmation button")
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass

    close_delay = max(0, int(config.get("close_delay_seconds", 2)))
    if close_delay:
        print(f"[submit] waiting {close_delay}s before final check and close")
        await page.wait_for_timeout(close_delay * 1000)

    await save_qr_code_image(page, config, config_path, index)

    errors = await detect_submit_errors(page)
    if errors:
        print(f"[stop] submit validation errors remain: {', '.join(errors)}")
        await wait_for_manual_review(page, config)
        return 5
    print("[done] page title:", await page.title())
    print("[done] page url:", page.url)
    return 0


async def run(config_path: Path) -> int:
    config = load_config(config_path)
    items = questionnaire_configs(config)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=bool(config.get("headless", False)),
            slow_mo=int(config.get("slow_mo_ms", 0)),
        )
        context = await browser.new_context()

        for index, item in enumerate(items, 1):
            page = await context.new_page()
            code = await process_questionnaire(page, item, config_path, index, len(items))
            if code:
                await browser.close()
                return code
            await page.close()

        await browser.close()
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill a questionnaire from config.json.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.json")),
        help="Path to the JSON config file.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(Path(args.config))))


if __name__ == "__main__":
    main()
