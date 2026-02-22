from __future__ import annotations

from dataclasses import dataclass

from .api_client import MetaAdsApiClient


@dataclass
class MetaCampaignManager:
    client: MetaAdsApiClient

    def list_campaigns(self) -> list[dict]:
        """占位：后续实现 campaign 列表获取。"""
        return []

