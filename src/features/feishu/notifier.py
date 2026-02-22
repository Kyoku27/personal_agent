from __future__ import annotations

from dataclasses import dataclass

from src.core.config_manager import get_env

from .bot_client import FeishuBotClient, send_text_to_open_id


@dataclass
class FeishuNotifier:
    client: FeishuBotClient

    def notify(self, message: str) -> None:
        """向默认接收人发送飞书文本通知。"""
        default_open_id = get_env("FEISHU_DEFAULT_USER_OPEN_ID") or ""
        if not default_open_id:
            # 没有配置接收人就直接返回，避免异常中断主流程。
            return
        send_text_to_open_id(default_open_id, message)

