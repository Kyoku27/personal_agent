from __future__ import annotations

from dataclasses import dataclass

from .api_client import ShopifyApiClient


@dataclass
class ShopifyInventoryManager:
    client: ShopifyApiClient

    def update_stock(self, inventory_item_id: str, available: int) -> None:
        """占位：后续实现库存更新。"""
        _ = (inventory_item_id, available)

