from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from src.core.config_manager import load_yaml_config

from .bot_client import _get_tenant_access_token, FeishuBotClient, FEISHU_BASE_URL


@dataclass
class FeishuSheetManager:
    client: FeishuBotClient

    def _get_revenue_bitable_conf(self) -> dict[str, Any]:
        cfg = load_yaml_config()
        return (cfg.get("feishu") or {}).get("revenue_bitable") or {}

    def upsert_monthly_revenue(self, app_token: str | None, table_id: str | None, year: int, month: int, data: dict) -> None:
        """向多维表格写入一条月度营业额记录（当前实现为简单追加）。"""
        conf = self._get_revenue_bitable_conf()
        app_token = app_token or conf.get("app_token") or ""
        table_id = table_id or conf.get("table_id") or ""
        if not app_token or not table_id:
            raise RuntimeError("未配置 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID 或 config.yaml.feishu.revenue_bitable")

        date_field = conf.get("date_field", "month")
        revenue_field = conf.get("revenue_field", "revenue")
        orders_field = conf.get("orders_field", "orders")
        platform_field = conf.get("platform_field", "platform")
        aov_field = conf.get("avg_order_value_field", "avg_order_value")

        month_str = f"{year:04d}-{month:02d}"
        fields = {
            date_field: month_str,
            revenue_field: data.get("revenue"),
            orders_field: data.get("orders"),
            platform_field: data.get("platform", "rakuten"),
            aov_field: data.get("avg_order_value"),
        }

        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {"fields": fields}
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"写入飞书多维表格失败: {data}")

