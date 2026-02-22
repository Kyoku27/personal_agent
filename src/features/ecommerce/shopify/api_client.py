from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ShopifyApiClient:
    api_key: str
    password: str
    shop_name: str

    def ping(self) -> bool:
        """占位：后续实现 Shopify Admin API 连通性检查。"""
        return all([self.api_key, self.password, self.shop_name])

