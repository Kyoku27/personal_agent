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

    def _search_record_by_sku(self, app_token: str, table_id: str, sku: str) -> str | None:
        """根据 SKU（商品名）搜索表格中的现有记录，返回记录的 ID_record。如果没有找到则返回 None。"""
        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        
        # 假设用来匹配的列名叫 "商品名"
        conf = self._get_revenue_bitable_conf()
        sku_field = conf.get("sku_field", "商品名")
        
        payload = {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": sku_field,
                        "operator": "is",
                        "value": [sku]
                    }
                ]
            }
        }
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                items = data.get("data", {}).get("items", [])
                if items:
                    return items[0].get("record_id")
        except Exception as e:
            print(f"搜索飞书记录失败 ({sku}): {e}")
        
        return None

    def upsert_pivot_revenue_record(self, app_token: str | None, table_id: str | None, target_date: Any, data: dict) -> None:
        """向图表按天更新 SKU 的营业额透视记录（横表逻辑）。"""
        conf = self._get_revenue_bitable_conf()
        app_token = app_token or conf.get("app_token") or ""
        table_id = table_id or conf.get("table_id") or ""
        if not app_token or not table_id:
            raise RuntimeError("未配置 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID 或 config.yaml.feishu.revenue_bitable")

        # Excel 对应的列名： 商品名 (SKU), 1日, 2日, 3日...
        sku_field = conf.get("sku_field", "商品名")
        sku_val = data.get("sku", "")
        if not sku_val:
            return

        # 获取日期代表的"天"
        day_number = target_date.day
        # 当天的营业额要在对应的 "x日" 列更新
        day_field = f"{day_number}日"
        revenue_val = data.get("revenue", 0.0)

        # 接下来要组成我们要更新或插入的字段
        fields = {
            sku_field: sku_val,
            day_field: revenue_val,
        }

        # 搜索这行 SKU 是否已经存在
        token = _get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        
        existing_record_id = self._search_record_by_sku(app_token, table_id, sku_val)
        
        if existing_record_id:
            # 存在 -> Update
            url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{existing_record_id}"
            payload = {"fields": fields}
            resp = requests.put(url, headers=headers, json=payload, timeout=10)
        else:
            # 不存在 -> Create (会在底部新建一个商品)
            url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            payload = {"fields": fields}
            resp = requests.post(url, headers=headers, json=payload, timeout=10)

        resp.raise_for_status()
        resp_data = resp.json()
        if resp_data.get("code") != 0:
            raise RuntimeError(f"更新飞书多维表格(透视表)失败: {resp_data}")

    def list_table_fields(self, app_token: str, table_id: str) -> list[str]:
        """通过获取一条记录的字段来推断表格的所有列名（使用 base:record:retrieve 权限）。"""
        token = _get_tenant_access_token()
        print(f"  ✅ token 获取成功: {token[:20]}...")
        # 获取最多 1 条记录，从字段 key 里推断列名
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"page_size": 1}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"  HTTP 状态码: {resp.status_code}")
        if not resp.ok:
            print(f"  响应内容: {resp.text[:500]}")
            resp.raise_for_status()

        data = resp.json()
        items = data.get("data", {}).get("items", [])
        if not items:
            print("  ⚠️ 表格暂无数据，无法推断列名。请先手动添加至少一行数据。")
            return []

        # 从第一条记录的 fields 字段中提取所有列名
        field_names = list(items[0].get("fields", {}).keys())
        print(f"\n=== Columns in table {table_id} ===")
        for name in field_names:
            print(f"  - {name}")
        return field_names

