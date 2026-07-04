from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from Crypto.Cipher import AES


FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    pass


@dataclass
class FeishuSettings:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    default_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "FeishuSettings":
        return cls(
            app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
            encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", "").strip(),
            default_chat_id=os.getenv("FEISHU_DEFAULT_CHAT_ID", "").strip(),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)


class FeishuClient:
    def __init__(self, settings: FeishuSettings | None = None):
        self.settings = settings or FeishuSettings.from_env()
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0

    def tenant_access_token(self) -> str:
        if not self.settings.enabled:
            raise FeishuError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
        if self._tenant_token and time.time() < self._tenant_token_expires_at - 300:
            return self._tenant_token

        response = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.settings.app_id, "app_secret": self.settings.app_secret},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuError(f"获取 tenant_access_token 失败：{payload}")
        self._tenant_token = str(payload["tenant_access_token"])
        self._tenant_token_expires_at = time.time() + int(payload.get("expire", 7200))
        return self._tenant_token

    def send_message(self, chat_id: str, card: dict[str, Any]) -> str:
        if not chat_id:
            raise FeishuError("缺少飞书 chat_id，请先在私人群聊中发送“绑定”")
        token = self.tenant_access_token()
        response = requests.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuError(f"发送飞书消息失败：{payload}")
        return str((payload.get("data") or {}).get("message_id") or "")

    def send_text(self, chat_id: str, text: str) -> str:
        token = self.tenant_access_token()
        response = requests.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuError(f"发送飞书文本失败：{payload}")
        return str((payload.get("data") or {}).get("message_id") or "")


def decrypt_payload_if_needed(payload: dict[str, Any], encrypt_key: str) -> dict[str, Any]:
    if "encrypt" not in payload:
        return payload
    if not encrypt_key:
        raise FeishuError("收到加密回调，但 FEISHU_ENCRYPT_KEY 未配置")
    encrypted = base64.b64decode(str(payload["encrypt"]))
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = encrypted[: AES.block_size]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    raw = cipher.decrypt(encrypted[AES.block_size :])
    pad = raw[-1]
    if pad < 1 or pad > AES.block_size:
        raise FeishuError("飞书加密回调解密失败：padding 非法")
    decoded = raw[:-pad].decode("utf-8")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise FeishuError("飞书加密回调解密成功，但内容不是 JSON") from exc


def callback_token(payload: dict[str, Any]) -> str:
    return str(
        payload.get("token")
        or (payload.get("header") or {}).get("token")
        or (payload.get("event") or {}).get("token")
        or ""
    )


def verify_callback_token(payload: dict[str, Any], expected: str) -> None:
    if expected and callback_token(payload) != expected:
        raise FeishuError("飞书回调 Verification Token 不匹配")


def is_url_verification(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "url_verification" or bool(payload.get("challenge"))


def event_type(payload: dict[str, Any]) -> str:
    return str((payload.get("header") or {}).get("event_type") or payload.get("type") or "")


def extract_message_event(payload: dict[str, Any]) -> tuple[str, str, str]:
    event = payload.get("event") or {}
    message = event.get("message") or {}
    chat_id = str(message.get("chat_id") or "")
    message_type = str(message.get("message_type") or "")
    content = message.get("content") or ""
    text = ""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            text = str(parsed.get("text") or content)
        except json.JSONDecodeError:
            text = content
    sender = event.get("sender") or {}
    sender_id = ""
    if isinstance(sender.get("sender_id"), dict):
        sender_id = str(sender["sender_id"].get("open_id") or sender["sender_id"].get("user_id") or "")
    return chat_id, message_type, text.strip() or sender_id


def extract_card_action(payload: dict[str, Any]) -> tuple[str, str, str]:
    event = payload.get("event") or {}
    action = event.get("action") or payload.get("action") or {}
    value = action.get("value") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    operator = event.get("operator") or {}
    operator_id = ""
    if isinstance(operator.get("open_id"), str):
        operator_id = operator["open_id"]
    elif isinstance(operator.get("operator_id"), dict):
        operator_id = str(operator["operator_id"].get("open_id") or "")
    return str(value.get("opportunity_id") or ""), str(value.get("decision") or ""), operator_id


def opportunity_card(opportunity: dict[str, Any]) -> dict[str, Any]:
    status_label = {
        "available": "课表判断：有空",
        "conflict": "课表判断：冲突",
        "unknown_time": "课表判断：时间不确定",
    }.get(str(opportunity.get("schedule_status")), "课表判断：未知")
    content = (
        f"**类型**：{opportunity.get('category_label') or opportunity.get('category')}\n"
        f"**活动**：{opportunity.get('title')}\n"
        f"**时间**：{opportunity.get('activity_time') or '未识别到明确活动时间'}\n"
        f"**地点**：{opportunity.get('location') or '未识别到地点'}\n"
        f"**报名链接**：{opportunity.get('signup_url') or '未识别到'}\n"
        f"**{status_label}**\n"
    )
    if opportunity.get("matched_time_text"):
        content += f"\n**匹配空闲**：\n{opportunity.get('matched_time_text')}\n"
    content += f"\n[查看原文]({opportunity.get('article_url')})"

    def button(label: str, decision: str, button_type: str = "default") -> dict[str, Any]:
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": button_type,
            "behaviors": [
                {
                    "type": "callback",
                    "value": {
                        "action": "opportunity_decision",
                        "opportunity_id": opportunity["id"],
                        "decision": decision,
                    },
                }
            ],
        }

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "发现校园机会"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": content},
                {
                    "tag": "action",
                    "actions": [
                        button("参加并报名", "join", "primary"),
                        button("不参加", "reject", "default"),
                        button("需要人工看看", "manual", "danger"),
                    ],
                },
            ]
        },
    }


def callback_toast(content: str, toast_type: str = "success") -> dict[str, Any]:
    return {"toast": {"type": toast_type, "content": content}}
