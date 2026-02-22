from __future__ import annotations

from dataclasses import dataclass

from .api_client import ShopifyApiClient


@dataclass
class ShopifyDataAnalyzer:
    client: ShopifyApiClient

    def get_revenue_summary(self, year: int, month: int) -> dict:
        """占位：后续实现按月营业额/订单等汇总。"""
        _ = (year, month)
        return {}

