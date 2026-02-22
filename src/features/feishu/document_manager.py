from __future__ import annotations

from dataclasses import dataclass

from .bot_client import FeishuBotClient


@dataclass
class FeishuDocumentManager:
    client: FeishuBotClient

    def update_table(self, doc_id: str, table_data: dict) -> None:
        """占位：后续实现更新文档/表格。"""
        _ = (doc_id, table_data)

