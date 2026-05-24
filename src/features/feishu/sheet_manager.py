from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .bot_client import FEISHU_BASE_URL, FeishuBotClient, _get_tenant_access_token


@dataclass
class FeishuSheetManager:
    client: FeishuBotClient

    def _headers(self) -> dict[str, str]:
        token = _get_tenant_access_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

    def list_table_fields(self, app_token: str, table_id: str) -> list[str]:
        headers = self._headers()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        resp = requests.get(url, headers=headers, params={"page_size": 200}, timeout=15)
        if resp.ok:
            data = resp.json()
            if data.get("code") == 0:
                return [item["field_name"] for item in (data.get("data") or {}).get("items") or [] if item.get("field_name")]

        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        resp = requests.get(url, headers=headers, params={"page_size": 1}, timeout=15)
        if not resp.ok:
            raise RuntimeError(f"Lark API HTTP {resp.status_code} GET records: {resp.text}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark list records failed: {data}")
        items = (data.get("data") or {}).get("items") or []
        return list((items[0].get("fields") or {}).keys()) if items else []

    def list_bitable_records(self, app_token: str, table_id: str, page_size: int = 500) -> list[dict[str, Any]]:
        headers = self._headers()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            if not resp.ok:
                raise RuntimeError(f"Lark API HTTP {resp.status_code} GET records: {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Lark list records failed: {data}")
            payload = data.get("data") or {}
            records.extend(payload.get("items") or [])
            if not payload.get("has_more"):
                break
            page_token = payload.get("page_token") or ""
        return records

    def _batch_create_records(self, app_token: str, table_id: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        headers = self._headers()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        for i in range(0, len(rows), 500):
            resp = requests.post(url, headers=headers, json={"records": [{"fields": fields} for fields in rows[i:i + 500]]}, timeout=30)
            if not resp.ok:
                raise RuntimeError(f"Lark API HTTP {resp.status_code} batch_create: {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Lark batch_create failed: {data}")

    def _batch_update_records(self, app_token: str, table_id: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        headers = self._headers()
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
        for i in range(0, len(rows), 500):
            resp = requests.post(url, headers=headers, json={"records": rows[i:i + 500]}, timeout=30)
            if not resp.ok:
                raise RuntimeError(f"Lark API HTTP {resp.status_code} batch_update: {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Lark batch_update failed: {data}")

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if isinstance(value, list) and len(value) == 1:
            return FeishuSheetManager._normalize_value(value[0])
        if isinstance(value, dict):
            return value.get("text") or value.get("name") or value.get("value") or value
        return value

    @classmethod
    def _fields_changed(cls, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        for key, value in incoming.items():
            if cls._normalize_value(existing.get(key)) != cls._normalize_value(value):
                return True
        return False

    @staticmethod
    def _existing_key(fields: dict[str, Any]) -> str | None:
        order_no = str(fields.get("\u6ce8\u6587\u756a\u53f7") or fields.get("\u53d7\u6ce8\u756a\u53f7") or "").strip()
        sku = str(fields.get("\u5546\u54c1\u7ba1\u7406\u756a\u53f7") or fields.get("SKU\u7ba1\u7406\u756a\u53f7") or fields.get("SKU") or fields.get("\u5546\u54c1\u540d") or "").strip()
        return f"{order_no}|{sku}" if order_no else None

    def bitable_bulk_upsert_records(self, app_token: str, table_id: str, records: list[tuple[dict[str, Any], dict[str, str]]], dry_run: bool = False) -> dict[str, int]:
        existing_records = self.list_bitable_records(app_token, table_id)
        existing_by_key = {key: item for item in existing_records if (key := self._existing_key(item.get("fields") or {}))}
        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        skipped = 0
        for fields, key_fields in records:
            key = f"{key_fields.get('order_number', '')}|{key_fields.get('sku', '')}"
            existing = existing_by_key.get(key)
            if not existing:
                creates.append(fields)
            elif self._fields_changed(existing.get("fields") or {}, fields):
                updates.append({"record_id": existing["record_id"], "fields": fields})
            else:
                skipped += 1
        if not dry_run:
            self._batch_create_records(app_token, table_id, creates)
            self._batch_update_records(app_token, table_id, updates)
        return {
            "created": 0 if dry_run else len(creates),
            "updated": 0 if dry_run else len(updates),
            "skipped": skipped,
            "planned_creates": len(creates),
            "planned_updates": len(updates),
            "existing_records_scanned": len(existing_records),
        }

    def upsert_pivot_revenue_record(self, app_token: str | None, table_id: str | None, target_date: Any, data: dict) -> None:
        raise RuntimeError("Legacy revenue/pivot sync is disabled. Use Rakuten order-detail sync.")
