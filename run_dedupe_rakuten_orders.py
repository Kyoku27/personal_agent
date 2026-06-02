from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from run_rakuten_orders import _store_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token
from src.features.feishu.sheet_manager import FeishuSheetManager
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable

JST = timezone(timedelta(hours=9))


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_tenant_access_token()}", "Content-Type": "application/json; charset=utf-8"}


def _date_text(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, JST).date().isoformat()
    return str(value or "")


def _norm(value: Any) -> str:
    return str(value or "").replace("-", "").strip().upper()


def _row_score(fields: dict[str, Any]) -> int:
    system_sku = _norm(fields.get("システム連携用SKU番号") or fields.get("システムSKU"))
    sku = _norm(fields.get("SKU管理番号") or fields.get("商品管理番号") or fields.get("SKU"))
    score = 0
    if system_sku and sku == system_sku:
        score += 100
    for field in ("性別", "年齢段", "購入時", "購入時間帯", "出生年"):
        if fields.get(field) not in (None, "", []):
            score += 1
    return score


def _delete_records(app_token: str, table_id: str, record_ids: list[str]) -> None:
    if not record_ids:
        return
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    for i in range(0, len(record_ids), 500):
        resp = requests.post(url, headers=_headers(), json={"records": record_ids[i:i + 500]}, timeout=30)
        if not resp.ok:
            raise RuntimeError(f"Lark API HTTP {resp.status_code} batch_delete: {resp.text}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark batch_delete failed: {data}")


def dedupe_rakuten_orders(store_id: str, start_date: str, end_date: str, execute: bool = False) -> dict[str, Any]:
    require_store_specific = store_id not in {"", "default"}
    table_id = _store_env(store_id, "ORDER_TABLE_ID", "FEISHU_RAKUTEN_ORDER_TABLE_ID", require_store_specific=require_store_specific)
    app_token = resolve_wiki_to_bitable(
        node_token=_store_env(store_id, "WIKI_NODE_TOKEN", "FEISHU_RAKUTEN_WIKI_NODE_TOKEN", require_store_specific=require_store_specific),
        direct_app_token=_store_env(store_id, "BITABLE_APP_TOKEN", "FEISHU_RAKUTEN_BITABLE_APP_TOKEN"),
    )
    manager = FeishuSheetManager(client=None)  # type: ignore[arg-type]
    records = manager.list_bitable_records(app_token, table_id)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    for record in records:
        fields = record.get("fields") or {}
        order_date = _date_text(fields.get("注文日") or fields.get("受注日"))
        if not (start_date <= order_date <= end_date):
            continue
        order_no = str(fields.get("注文番号") or fields.get("受注番号") or "").strip()
        system_sku = str(fields.get("システム連携用SKU番号") or fields.get("システムSKU") or "").strip()
        if not order_no or not system_sku:
            continue
        scanned += 1
        groups[f"{order_no}|{system_sku}"].append(record)

    duplicate_groups = {key: rows for key, rows in groups.items() if len(rows) > 1}
    delete_ids: list[str] = []
    preview: list[dict[str, Any]] = []
    for key, rows in duplicate_groups.items():
        sorted_rows = sorted(rows, key=lambda row: _row_score(row.get("fields") or {}), reverse=True)
        keep = sorted_rows[0]
        deletes = sorted_rows[1:]
        delete_ids.extend(str(row["record_id"]) for row in deletes if row.get("record_id"))
        preview.append(
            {
                "key": key,
                "keep_record_id": keep.get("record_id"),
                "delete_record_ids": [row.get("record_id") for row in deletes],
            }
        )

    if execute:
        _delete_records(app_token, table_id, delete_ids)

    return {
        "success": True,
        "store_id": store_id,
        "start_date": start_date,
        "end_date": end_date,
        "scanned": scanned,
        "duplicate_groups": len(duplicate_groups),
        "planned_deletes": len(delete_ids),
        "deleted": len(delete_ids) if execute else 0,
        "dry_run": not execute,
        "preview": preview[:50],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", default="store2")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(dedupe_rakuten_orders(args.store_id, args.start_date, args.end_date, args.execute), ensure_ascii=False, indent=2))
