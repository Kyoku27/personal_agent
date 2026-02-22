from __future__ import annotations

from dataclasses import dataclass

from .api_client import RakutenApiClient


@dataclass
class RakutenPromotionManager:
    client: RakutenApiClient

    def set_promotion(self, promotion_id: str, enabled: bool) -> None:
        """占位：后续实现促销开关/配置。"""
        _ = (promotion_id, enabled)

