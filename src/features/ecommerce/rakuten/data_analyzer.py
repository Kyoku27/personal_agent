from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .api_client import RakutenApiClient


@dataclass
class RakutenDataAnalyzer:
    client: RakutenApiClient

    def _month_range(self, year: int, month: int) -> tuple[date, date]:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        return start, end - timedelta(days=1)

    def get_revenue_summary(self, year: int, month: int) -> dict:
        """按月汇总乐天营业额的占位实现，返回统一结构。

        当前版本不调用真实 API，而是基于占位数据返回 0 值，
        方便先把整体工作流串起来。
        """
        start, end = self._month_range(year, month)
        _ = (start, end, self.client)

        revenue = 0.0
        orders = 0
        avg_order_value = 0.0

        return {
            "platform": "rakuten",
            "year": year,
            "month": month,
            "revenue": revenue,
            "orders": orders,
            "avg_order_value": avg_order_value,
        }

