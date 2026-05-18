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

    def _search_record_by_keys(
        self, app_token: str, table_id: str, key_fields: dict[str, str]
    ) -> str | None:
        """根据多个 key 字段搜索现有记录，返回 record_id（找不到返回 None）。"""
        if not key_fields:
            return None

        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        conditions = []
        for field_name, value in key_fields.items():
            if value is None:
                continue
            v = str(value).strip()
            if not v:
                continue
            conditions.append(
                {
                    "field_name": field_name,
                    "operator": "is",
                    "value": [v],
                }
            )

        if not conditions:
            return None

        payload = {"filter": {"conjunction": "and", "conditions": conditions}}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                items = data.get("data", {}).get("items", [])
                if items:
                    return items[0].get("record_id")
        except Exception as e:
            print(f"搜索飞书记录失败 keys={key_fields}: {e}")

        return None

    def bitable_upsert_record(
        self,
        app_token: str,
        table_id: str,
        *,
        fields: dict[str, Any],
        key_fields: dict[str, str],
    ) -> str:
        """对 bitable 做 upsert：按 key_fields 查找，有则 update，无则 create。返回 record_id。"""
        token = _get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        existing_record_id = self._search_record_by_keys(app_token, table_id, key_fields)

        if existing_record_id:
            url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{existing_record_id}"
            payload = {"fields": fields}
            resp = requests.put(url, headers=headers, json=payload, timeout=12)
        else:
            url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            payload = {"fields": fields}
            resp = requests.post(url, headers=headers, json=payload, timeout=12)

        resp.raise_for_status()
        resp_data = resp.json()
        if resp_data.get("code") != 0:
            raise RuntimeError(f"bitable upsert 失败: {resp_data}")

        record_id = (
            (resp_data.get("data") or {}).get("record") or {}
        ).get("record_id") or existing_record_id
        return record_id or ""

    def list_bitable_records(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        """Read all records once so callers can build a local index instead of searching row-by-row."""
        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"读取飞书记录失败: {data}")

            body = data.get("data") or {}
            records.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break

        return records

    @staticmethod
    def _normalize_cell_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else str(value)
        if isinstance(value, list):
            return ",".join(FeishuSheetManager._normalize_cell_value(v) for v in value)
        if isinstance(value, dict):
            return str(value.get("text") or value.get("name") or value.get("value") or value).strip()
        return str(value).strip()

    @classmethod
    def _record_key(cls, fields: dict[str, Any], key_names: list[str]) -> tuple[str, ...]:
        return tuple(cls._normalize_cell_value(fields.get(name)) for name in key_names)

    @classmethod
    def _fields_changed(cls, old_fields: dict[str, Any], new_fields: dict[str, Any]) -> bool:
        for name, new_value in new_fields.items():
            if cls._normalize_cell_value(old_fields.get(name)) != cls._normalize_cell_value(new_value):
                return True
        return False

    @staticmethod
    def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

    def _batch_create_records(
        self,
        app_token: str,
        table_id: str,
        records: list[dict[str, Any]],
    ) -> int:
        if not records:
            return 0
        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        created = 0
        for chunk in self._chunks(records, 500):
            resp = requests.post(url, headers=headers, json={"records": chunk}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"批量新增飞书记录失败: {data}")
            created += len(chunk)
        return created

    def _batch_update_records(
        self,
        app_token: str,
        table_id: str,
        records: list[dict[str, Any]],
    ) -> int:
        if not records:
            return 0
        token = _get_tenant_access_token()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        updated = 0
        for chunk in self._chunks(records, 500):
            resp = requests.post(url, headers=headers, json={"records": chunk}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"批量更新飞书记录失败: {data}")
            updated += len(chunk)
        return updated

    def bitable_bulk_upsert_records(
        self,
        app_token: str,
        table_id: str,
        *,
        records: list[tuple[dict[str, Any], dict[str, str]]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Plan and optionally write records using one table scan plus batch create/update."""
        if not records:
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "planned_creates": 0,
                "planned_updates": 0,
                "existing_records_scanned": 0,
                "creates": [],
                "updates": [],
                "unchanged": [],
                "duplicate_keys": [],
                "existing_records": [],
                "key_names": [],
            }

        key_names = list(records[0][1].keys())
        existing_records = self.list_bitable_records(app_token, table_id)
        existing_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for record in existing_records:
            fields = record.get("fields") or {}
            key = self._record_key(fields, key_names)
            if all(key):
                existing_by_key[key] = record

        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []
        duplicate_keys: list[dict[str, str]] = []
        seen_incoming: set[tuple[str, ...]] = set()

        for fields, key_fields in records:
            key = self._record_key(key_fields, key_names)
            if key in seen_incoming:
                duplicate_keys.append(key_fields)
                continue
            seen_incoming.add(key)

            existing = existing_by_key.get(key)
            if not existing:
                creates.append({"fields": fields, "key_fields": key_fields})
                continue

            existing_fields = existing.get("fields") or {}
            record_id = existing.get("record_id")
            if record_id and self._fields_changed(existing_fields, fields):
                updates.append({"record_id": record_id, "fields": fields, "key_fields": key_fields})
            else:
                unchanged.append({"record_id": record_id, "fields": fields, "key_fields": key_fields})

        created = 0
        updated = 0
        if not dry_run:
            created = self._batch_create_records(
                app_token,
                table_id,
                [{"fields": item["fields"]} for item in creates],
            )
            updated = self._batch_update_records(
                app_token,
                table_id,
                [{"record_id": item["record_id"], "fields": item["fields"]} for item in updates],
            )

        return {
            "created": created,
            "updated": updated,
            "skipped": len(unchanged),
            "planned_creates": len(creates),
            "planned_updates": len(updates),
            "duplicate_keys": duplicate_keys,
            "existing_records_scanned": len(existing_records),
            "creates": creates,
            "updates": updates,
            "unchanged": unchanged,
            "existing_records": existing_records,
            "key_names": key_names,
        }

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
        """Return table field names. Prefer metadata API; fallback to one-record inference."""
        token = _get_tenant_access_token()
        print(f"  ✅ token 获取成功: {token[:20]}...")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        fields_url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        try:
            field_resp = requests.get(fields_url, headers=headers, params={"page_size": 500}, timeout=10)
            if field_resp.ok:
                field_data = field_resp.json()
                if field_data.get("code") == 0:
                    items = (field_data.get("data") or {}).get("items") or []
                    field_names = [x.get("field_name") for x in items if x.get("field_name")]
                    if field_names:
                        print(f"\n=== Fields in table {table_id} ===")
                        for name in field_names:
                            print(f"  - {name}")
                        return field_names
            else:
                print(f"  字段接口不可用，回退到记录推断: {field_resp.status_code} {field_resp.text[:200]}")
        except Exception as e:
            print(f"  字段接口读取失败，回退到记录推断: {e}")

        # Fallback: 获取最多 1 条记录，从字段 key 里推断列名
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
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
