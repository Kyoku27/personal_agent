from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import Any

from src.core.config_manager import get_env

# The user provided a larksuite.com URL, so we must use the international API base URL
FEISHU_BASE_URL = "https://open.larksuite.com/open-apis"


def _get_tenant_access_token() -> str:
    app_id = get_env("FEISHU_APP_ID") or ""
    app_secret = get_env("FEISHU_APP_SECRET") or ""
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")

    url = f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    token = data.get("tenant_access_token") or ""
    if not token:
        raise RuntimeError("tenant_access_token 为空")
    return token


def send_text_to_open_id(open_id: str, text: str) -> None:
    token = _get_tenant_access_token()
    url = f"{FEISHU_BASE_URL}/im/v1/messages?receive_id_type=open_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": {"text": text},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"发送飞书消息失败: {data}")


@dataclass
class FeishuBotClient:
    bot_token: str

    def ping(self) -> bool:
        """占位：后续实现 token 校验/连通性检查。"""
        return bool(self.bot_token)

