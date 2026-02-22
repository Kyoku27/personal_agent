from __future__ import annotations

from dataclasses import dataclass

from .api_client import ShopifyApiClient


@dataclass
class ShopifyPromotionManager:
    client: ShopifyApiClient

    def set_discount(self, discount_id: str, enabled: bool) -> None:
        """占位：后续实现折扣/促销配置。"""
        _ = (discount_id, enabled)

