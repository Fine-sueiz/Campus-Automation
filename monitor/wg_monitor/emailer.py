from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .config import ConfigError, get_bool_env, get_int_env
from .extraction import ArticleAnalysis
from .schedule import format_free_blocks, format_time_windows, TimeBlock

PLACEHOLDER_VALUES = {
    "你的姓名",
    "你的手机号",
    "你的邮箱",
    "申请人",
    "your-email@qq.com",
}


@dataclass(frozen=True)
class EmailSettings:
    dry_run: bool
    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    sender: str
    notify_email: str

    @classmethod
    def from_env(cls) -> "EmailSettings":
        return cls(
            dry_run=get_bool_env("EMAIL_DRY_RUN", True),
            host=os.getenv("SMTP_HOST", "").strip(),
            port=get_int_env("SMTP_PORT", 465),
            use_ssl=get_bool_env("SMTP_USE_SSL", True),
            username=os.getenv("SMTP_USER", "").strip(),
            password=os.getenv("SMTP_PASSWORD", "").strip(),
            sender=os.getenv("EMAIL_SENDER", os.getenv("SMTP_USER", "")).strip(),
            notify_email=os.getenv("NOTIFY_EMAIL", "").strip(),
        )

    def validate(self, require_password: bool = True) -> list[str]:
        missing: list[str] = []
        if not self.host:
            missing.append("SMTP_HOST")
        if not self.username:
            missing.append("SMTP_USER")
        if require_password and not self.password:
            missing.append("SMTP_PASSWORD")
        if not self.sender:
            missing.append("EMAIL_SENDER")
        return missing


def validate_user_profile_for_email(app_config: dict[str, Any]) -> list[str]:
    user = app_config.get("user") or {}
    email_config = app_config.get("email") or {}
    required = email_config.get("required_profile_fields") or ["name", "phone", "contact_email"]
    labels = {
        "name": "姓名",
        "phone": "手机号",
        "student_id": "学号",
        "major": "专业",
        "contact_email": "联系邮箱",
    }
    missing: list[str] = []
    for field in required:
        key = str(field)
        value = str(user.get(key) or "").strip()
        if not value or value in PLACEHOLDER_VALUES or value.startswith("你的"):
            missing.append(labels.get(key, key))
    return missing


def compose_application_email(
    app_config: dict[str, Any],
    analysis: ArticleAnalysis,
    recipient: str,
    article_url: str,
    free_text: str,
    matched_windows: list[TimeBlock],
) -> EmailMessage:
    user = app_config.get("user") or {}
    subject_template = ((app_config.get("email") or {}).get("subject_template")) or "勤工助学岗位申请 - {name} - 可用时间"
    name = str(user.get("name") or "申请人")
    subject = subject_template.format(name=name)

    fallback_body = build_default_application_body(app_config, analysis, article_url, free_text, matched_windows)
    llm_body = maybe_generate_llm_application_body(
        app_config=app_config,
        analysis=analysis,
        recipient=recipient,
        article_url=article_url,
        free_text=free_text,
        matched_windows=matched_windows,
        fallback_body=fallback_body,
    )

    msg = EmailMessage()
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(llm_body or fallback_body)

    resume_path = str(user.get("resume_path") or "").strip()
    if resume_path:
        attach_file(msg, Path(resume_path))
    return msg


def compose_opportunity_application_email(
    app_config: dict[str, Any],
    opportunity: Any,
    recipient: str,
) -> EmailMessage:
    user = app_config.get("user") or {}
    email_config = app_config.get("email") or {}
    name = str(user.get("name") or "申请人")
    title = get_opportunity_value(opportunity, "title") or "校园机会"
    category = get_opportunity_value(opportunity, "category_label") or get_opportunity_value(opportunity, "category")
    subject_template = (
        email_config.get("opportunity_subject_template")
        or "校园机会申请 - {name} - {title}"
    )
    subject = subject_template.format(name=name, title=title[:40], category=category or "校园机会")

    fallback_body = build_default_opportunity_body(app_config, opportunity)
    article_analysis = ArticleAnalysis(
        title=title,
        text=get_opportunity_value(opportunity, "raw_text"),
        is_target=True,
        keyword_hits=[],
        emails=[recipient],
        position_title=title,
        deadline=get_opportunity_value(opportunity, "deadline"),
        time_windows=[],
        reasons=[],
    )
    llm_body = maybe_generate_llm_application_body(
        app_config=app_config,
        analysis=article_analysis,
        recipient=recipient,
        article_url=get_opportunity_value(opportunity, "article_url"),
        free_text=get_opportunity_value(opportunity, "free_time_text"),
        matched_windows=[],
        fallback_body=fallback_body,
    )

    msg = EmailMessage()
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(llm_body or fallback_body)

    resume_path = str(user.get("resume_path") or "").strip()
    if resume_path:
        attach_file(msg, Path(resume_path))
    return msg


def build_default_application_body(
    app_config: dict[str, Any],
    analysis: ArticleAnalysis,
    article_url: str,
    free_text: str,
    matched_windows: list[TimeBlock],
) -> str:
    user = app_config.get("user") or {}
    name = str(user.get("name") or "申请人")
    body_lines = [
        "老师/负责人您好：",
        "",
        f"我在公众号推文中看到“{analysis.position_title}”相关勤工助学岗位，想申请该岗位。",
        "",
        "我的基本信息：",
        f"- 姓名：{name}",
    ]
    if user.get("student_id"):
        body_lines.append(f"- 学号：{user.get('student_id')}")
    if user.get("major"):
        body_lines.append(f"- 专业：{user.get('major')}")
    if user.get("phone"):
        body_lines.append(f"- 手机：{user.get('phone')}")
    if user.get("contact_email"):
        body_lines.append(f"- 邮箱：{user.get('contact_email')}")

    body_lines.extend(["", "根据我目前的课表，我的可用时间如下：", free_text])
    if matched_windows:
        body_lines.extend(["", "其中与推文中工作时段匹配的时间：", format_time_windows(matched_windows)])
    if analysis.deadline:
        body_lines.extend(["", f"我注意到报名/申请时间信息：{analysis.deadline}"])
    body_lines.extend(
        [
            "",
            f"推文链接：{article_url}",
            "",
            "如需补充材料或面试，我可以继续配合。谢谢！",
            "",
            name,
        ]
    )
    return "\n".join(body_lines)


def build_default_opportunity_body(app_config: dict[str, Any], opportunity: Any) -> str:
    user = app_config.get("user") or {}
    name = str(user.get("name") or "申请人")
    title = get_opportunity_value(opportunity, "title") or "校园机会"
    article_url = get_opportunity_value(opportunity, "article_url")
    activity_time = get_opportunity_value(opportunity, "activity_time")
    location = get_opportunity_value(opportunity, "location")
    deadline = get_opportunity_value(opportunity, "deadline")
    free_text = get_opportunity_value(opportunity, "free_time_text")
    matched_text = get_opportunity_value(opportunity, "matched_time_text")

    body_lines = [
        "老师/负责人您好：",
        "",
        f"我看到“{title}”相关信息，希望申请/报名参加。",
        "",
        "我的基本信息：",
        f"- 姓名：{name}",
    ]
    if user.get("student_id"):
        body_lines.append(f"- 学号：{user.get('student_id')}")
    if user.get("major"):
        body_lines.append(f"- 专业：{user.get('major')}")
    if user.get("phone"):
        body_lines.append(f"- 手机：{user.get('phone')}")
    if user.get("contact_email"):
        body_lines.append(f"- 邮箱：{user.get('contact_email')}")

    if activity_time:
        body_lines.extend(["", f"我注意到活动/工作时间：{activity_time}"])
    if location:
        body_lines.append(f"地点信息：{location}")
    if deadline:
        body_lines.append(f"报名/申请截止信息：{deadline}")

    if matched_text:
        body_lines.extend(["", "根据我的课表，我可以匹配的时间如下：", matched_text])
    elif free_text:
        body_lines.extend(["", "根据我目前的课表，我的可用时间如下：", free_text])

    body_lines.extend(
        [
            "",
            f"原文链接：{article_url}",
            "",
            "如需补充材料或进一步确认信息，我可以继续配合。谢谢！",
            "",
            name,
        ]
    )
    return "\n".join(body_lines)


def maybe_generate_llm_application_body(
    app_config: dict[str, Any],
    analysis: ArticleAnalysis,
    recipient: str,
    article_url: str,
    free_text: str,
    matched_windows: list[TimeBlock],
    fallback_body: str,
) -> str:
    email_config = app_config.get("email") or {}
    mode = str(os.getenv("EMAIL_BODY_MODE") or email_config.get("body_mode") or "template").strip().lower()
    if mode not in {"llm", "ai", "api"}:
        return ""

    provider = str(os.getenv("EMAIL_LLM_PROVIDER") or email_config.get("llm_provider") or "deepseek").strip().lower()
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        endpoint = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions").strip()
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        endpoint = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions").strip()
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    else:
        print(f"  邮件正文 API 未启用：不支持的 EMAIL_LLM_PROVIDER={provider}")
        return ""

    if not api_key:
        print(f"  邮件正文 API 未启用：缺少 {provider.upper()} API key，改用本地模板。")
        return ""

    prompt = build_llm_application_prompt(
        app_config=app_config,
        analysis=analysis,
        recipient=recipient,
        article_url=article_url,
        free_text=free_text,
        matched_windows=matched_windows,
        fallback_body=fallback_body,
    )
    timeout = int(os.getenv("EMAIL_LLM_TIMEOUT_SECONDS", "25") or "25")

    try:
        import requests

        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一名中国大学本科生的邮件写作助手。"
                                "只输出邮件正文，不输出主题、收件人、解释或 Markdown。"
                                "内容要礼貌、自然、可信，不要编造经历，不要夸大承诺。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.45,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = str(data["choices"][0]["message"]["content"]).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"  邮件正文 API 生成失败，改用本地模板：{exc}")
        return ""

    cleaned = clean_llm_body(content)
    if len(cleaned) < 80:
        print("  邮件正文 API 返回内容过短，改用本地模板。")
        return ""
    return cleaned


def get_opportunity_value(opportunity: Any, key: str) -> str:
    if isinstance(opportunity, dict):
        return str(opportunity.get(key) or "")
    return str(getattr(opportunity, key, "") or "")


def build_llm_application_prompt(
    app_config: dict[str, Any],
    analysis: ArticleAnalysis,
    recipient: str,
    article_url: str,
    free_text: str,
    matched_windows: list[TimeBlock],
    fallback_body: str,
) -> str:
    user = app_config.get("user") or {}
    context_markdown = read_email_context_markdown(app_config)

    profile = {
        "name": user.get("name") or "申请人",
        "phone": user.get("phone") or "",
        "student_id": user.get("student_id") or "",
        "major": user.get("major") or "",
        "contact_email": user.get("contact_email") or "",
    }
    matched_text = format_time_windows(matched_windows) if matched_windows else ""
    return "\n".join(
        [
            "请根据以下信息，写一封可以直接发送的中文申请邮件正文。",
            "",
            "硬性要求：",
            "- 不要编造未提供的奖项、经历、证书、职务。",
            "- 如果推文没有明确时间，就列出我的完整可用时间。",
            "- 如果推文有明确时间，优先说明我能匹配的时间。",
            "- 周五只写 15:00 前有空，不要写周五 15:00 以后可以工作。",
            "- 保留礼貌开头、基本信息、可用时间、推文链接、结尾署名。",
            "",
            f"收件邮箱：{recipient}",
            f"推文链接：{article_url}",
            f"岗位/活动标题：{analysis.position_title or analysis.title}",
            f"截止时间：{analysis.deadline or '未识别到'}",
            f"关键词命中：{'、'.join(analysis.keyword_hits) or '无'}",
            "",
            "我的基本信息：",
            json.dumps(profile, ensure_ascii=False, indent=2),
            "",
            "从结构化课表计算出的可用时间：",
            truncate_text(free_text, 2500),
            "",
            "与推文明确时段匹配的时间：",
            matched_text or "推文未识别到明确星期+时段，或没有可匹配时段。",
            "",
            "个人课表 Markdown 说明：",
            truncate_text(context_markdown or "未配置。", 3500),
            "",
            "推文正文：",
            truncate_text(analysis.text, 9000),
            "",
            "本地模板参考，可以优化但不要改变事实：",
            truncate_text(fallback_body, 3500),
        ]
    )


def read_email_context_markdown(app_config: dict[str, Any]) -> str:
    email_config = app_config.get("email") or {}
    path_value = str(os.getenv("EMAIL_CONTEXT_MARKDOWN") or email_config.get("context_markdown") or "").strip()
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def truncate_text(text: str, limit: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n...[已截断]"


def clean_llm_body(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def compose_uncertain_notice(
    analysis: ArticleAnalysis,
    article_url: str,
    reasons: list[str],
    free_text: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"公众号岗位监测提醒：{analysis.title[:50] or '发现疑似岗位'}"
    body = [
        "发现一篇疑似勤工助学岗位推文，但未自动投递。",
        "",
        f"标题：{analysis.title}",
        f"链接：{article_url}",
        "",
        "原因：",
        *[f"- {reason}" for reason in reasons],
        "",
        "关键词命中：",
        "、".join(analysis.keyword_hits) or "无",
        "",
        "当前课表空闲时间：",
        free_text,
    ]
    msg.set_content("\n".join(body))
    return msg


def attach_file(msg: EmailMessage, path: Path) -> None:
    if not path.exists():
        raise ConfigError(f"简历附件不存在：{path}")
    data = path.read_bytes()
    msg.add_attachment(
        data,
        maintype="application",
        subtype="octet-stream",
        filename=path.name,
    )


def send_email(settings: EmailSettings, msg: EmailMessage, recipient: str) -> None:
    set_or_replace_header(msg, "From", settings.sender)
    set_or_replace_header(msg, "To", recipient)
    if settings.dry_run:
        print("=" * 72)
        print("[DRY RUN] 邮件未发送，以下是将要发送的内容：")
        print(f"From: {settings.sender}")
        print(f"To: {recipient}")
        print(f"Subject: {msg['Subject']}")
        print("-" * 72)
        print(get_plain_content(msg))
        print("=" * 72)
        return

    missing = settings.validate(require_password=True)
    if missing:
        raise ConfigError(f"邮件配置缺失：{', '.join(missing)}")

    if settings.use_ssl:
        with smtplib.SMTP_SSL(settings.host, settings.port, timeout=20) as smtp:
            smtp.login(settings.username, settings.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(settings.username, settings.password)
            smtp.send_message(msg)


def set_or_replace_header(msg: EmailMessage, name: str, value: str) -> None:
    if name in msg:
        msg.replace_header(name, value)
    else:
        msg[name] = value


def get_plain_content(msg: EmailMessage) -> str:
    if msg.is_multipart():
        body = msg.get_body(preferencelist=("plain",))
        return body.get_content() if body else ""
    return msg.get_content()
