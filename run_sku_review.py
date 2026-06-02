from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from run_dashboard_sheet_export import SKU_MONTHLY_TABLES, _normalized_keys, _number, _records, _text
from run_yearly_dashboard_charts import STORE_CONFIGS, _amount

JST = timezone(timedelta(hours=9))
STATUS_FILE = Path(__file__).resolve().parent / "logs" / "pending_sku_review.json"
STORE_LABELS = {"default": "EZLIFE", "store2": "tomtoc"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _sku_prefix(sku: str) -> str:
    text = _norm(sku).upper().replace("-", "")
    match = re.match(r"^([A-Z]{1,3}\d{1,3})", text)
    return match.group(1) if match else text


def _record_month(fields: dict[str, Any]) -> str:
    return _text(fields.get("月別"))


def _order_date_text(fields: dict[str, Any]) -> str:
    value = fields.get("注文日")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, JST).date().isoformat()
    return _text(value)


def _key_set_from_table(records: list[dict[str, Any]], key_fields: tuple[str, ...]) -> set[str]:
    keys: set[str] = set()
    for item in records:
        fields = item.get("fields") or {}
        for field_name in key_fields:
            keys.update(_normalized_keys(fields.get(field_name)))
    return {key.upper().replace("-", "") for key in keys if key}


def _spu_tag_key_set(records: list[dict[str, Any]]) -> set[str]:
    sku_keys: set[str] = set()
    for item in records:
        fields = item.get("fields") or {}
        sku_keys.update(_normalized_keys(fields.get("tag_spu")))
    return {key.upper().replace("-", "") for key in sku_keys if key}


def _order_sku(fields: dict[str, Any]) -> str:
    return _text(fields.get("システム連携用SKU番号") or fields.get("SKU管理番号"))


def _group_order_skus(store_id: str, start_date: str | None = None, end_date: str | None = None) -> dict[str, dict[str, Any]]:
    config = STORE_CONFIGS[store_id]
    grouped: dict[str, dict[str, Any]] = {}
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        order_date = _order_date_text(fields)
        if start_date and order_date and order_date < start_date:
            continue
        if end_date and order_date and order_date > end_date:
            continue
        sku = _order_sku(fields)
        if not sku:
            continue
        key = sku.upper().replace("-", "")
        entry = grouped.setdefault(
            key,
            {
                "sku": sku,
                "sample_product_name": _text(fields.get("商品名")),
                "suggested_spu": _sku_prefix(sku),
                "months": set(),
                "order_count": 0,
                "units": 0.0,
                "amount": 0.0,
                "latest_order_date": "",
            },
        )
        entry["months"].add(_record_month(fields))
        entry["order_count"] += 1
        entry["units"] += _number(fields.get("個数"))
        entry["amount"] += _amount(fields, config)
        if order_date and order_date > entry["latest_order_date"]:
            entry["latest_order_date"] = order_date
    return grouped


def inspect_pending_skus(
    store_ids: tuple[str, ...] = ("default", "store2"),
    start_date: str | None = None,
    end_date: str | None = None,
    write_status: bool = False,
) -> dict[str, Any]:
    stores: list[dict[str, Any]] = []
    total_pending = 0
    for store_id in store_ids:
        config = STORE_CONFIGS[store_id]
        sku_table = SKU_MONTHLY_TABLES[store_id]
        sku_tag_keys = _key_set_from_table(_records(config, sku_table), ("Tag",))
        sku_name_keys = _key_set_from_table(_records(config, sku_table), ("商品名",))
        spu_sku_keys = _spu_tag_key_set(_records(config, config.spu_monthly_table))
        order_skus = _group_order_skus(store_id, start_date=start_date, end_date=end_date)

        pending: list[dict[str, Any]] = []
        for normalized_sku, entry in sorted(order_skus.items(), key=lambda item: item[1]["latest_order_date"], reverse=True):
            suggested_spu = str(entry["suggested_spu"] or "")
            missing_sku_tag = normalized_sku not in sku_tag_keys
            missing_sku_name = normalized_sku not in sku_name_keys
            missing_spu_tag_spu = normalized_sku not in spu_sku_keys
            if not (missing_sku_tag or missing_sku_name or missing_spu_tag_spu):
                continue
            pending.append(
                {
                    "store_id": store_id,
                    "store_label": STORE_LABELS.get(store_id, store_id),
                    "sku": entry["sku"],
                    "suggested_spu": suggested_spu,
                    "sample_product_name": entry["sample_product_name"],
                    "months": sorted(month for month in entry["months"] if month),
                    "latest_order_date": entry["latest_order_date"],
                    "order_count": entry["order_count"],
                    "units": entry["units"],
                    "amount": entry["amount"],
                    "missing": {
                        "sku_tag": missing_sku_tag,
                        "sku_name": missing_sku_name,
                        "spu_tag_spu": missing_spu_tag_spu,
                    },
                }
            )

        total_pending += len(pending)
        stores.append(
            {
                "store_id": store_id,
                "store_label": STORE_LABELS.get(store_id, store_id),
                "orders_sku_count": len(order_skus),
                "pending_count": len(pending),
                "pending": pending,
            }
        )

    payload = {
        "success": True,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "start_date": start_date,
        "end_date": end_date,
        "total_pending": total_pending,
        "stores": stores,
    }
    if write_status:
        STATUS_FILE.parent.mkdir(exist_ok=True)
        STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def read_pending_sku_status() -> dict[str, Any]:
    if not STATUS_FILE.exists():
        return inspect_pending_skus(write_status=True)
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return inspect_pending_skus(write_status=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stores", nargs="+", default=["default", "store2"])
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--write-status", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            inspect_pending_skus(
                store_ids=tuple(args.stores),
                start_date=args.start_date,
                end_date=args.end_date,
                write_status=args.write_status,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
