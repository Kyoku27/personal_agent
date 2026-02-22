from __future__ import annotations

from dataclasses import dataclass

from .api_client import RakutenApiClient


@dataclass
class RakutenInventoryManager:
    client: RakutenApiClient

    def update_stock(self, sku: str, quantity: int) -> None:
        """占位：后续实现库存更新。"""
        _ = (sku, quantity)

