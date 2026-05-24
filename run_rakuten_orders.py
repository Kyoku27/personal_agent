from __future__ import annotations

import re
import os
from datetime import date, datetime, timedelta
from typing import Any

from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.ecommerce.rakuten.order_sync import build_records, decide_granularity
from src.features.feishu.bot_client import FeishuBotClient
from src.features.feishu.sheet_manager import FeishuSheetManager
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable


def _store_env_prefix(store_id: str | None) -> str:
    if not store_id or store_id == "default":
        return ""
    return re.sub(r"[^A-Z0-9]+", "_", store_id.upper()).strip("_")


def _store_env(store_id: str | None, suffix: str, default_key: str, require_store_specific: bool = False) -> str:
    prefix = _store_env_prefix(store_id)
    if prefix:
        store_key = f"FEISHU_RAKUTEN_{prefix}_{suffix}"
        value = get_env(store_key, "") or ""
        if value:
            return value
        if require_store_specific:
            raise RuntimeError(f"{store_key} is not configured")
        return ""
    return get_env(default_key, "") or ""


def _parse_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _resolve_range(date_str: str | None = None, start_date: str | None = None, end_date: str | None = None) -> tuple[str, str]:
    if start_date or end_date:
        if not start_date or not end_date:
            raise ValueError("start_date and end_date must be provided together")
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
    elif date_str:
        start = end = _parse_date(date_str, "date")
    else:
        start = end = date.today() - timedelta(days=1)
    if start > end:
        raise ValueError("start_date cannot be after end_date")
    return start.isoformat(), end.isoformat()


def run_rakuten_orders_sync(
    date_str: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    inspect: bool = False,
    store_id: str = "default",
) -> dict[str, Any]:
    require_store_specific = bool(_store_env_prefix(store_id))
    table_id = _store_env(
        store_id,
        "ORDER_TABLE_ID",
        "FEISHU_RAKUTEN_ORDER_TABLE_ID",
        require_store_specific=require_store_specific,
    )
    if not table_id:
        raise RuntimeError(f"Rakuten store '{store_id}' order table is not configured")

    app_token = resolve_wiki_to_bitable(
        node_token=_store_env(
            store_id,
            "WIKI_NODE_TOKEN",
            "FEISHU_RAKUTEN_WIKI_NODE_TOKEN",
            require_store_specific=require_store_specific,
        ),
        direct_app_token=_store_env(store_id, "BITABLE_APP_TOKEN", "FEISHU_RAKUTEN_BITABLE_APP_TOKEN"),
    )
    sheet_manager = FeishuSheetManager(client=FeishuBotClient(bot_token=get_env("FEISHU_BOT_TOKEN", "") or "dummy"))
    columns = sheet_manager.list_table_fields(app_token, table_id)
    granularity = decide_granularity(columns)
    if inspect:
        return {
            "success": True,
            "message": f"Target table schema loaded: {len(columns)} columns",
            "store_id": store_id,
            "columns": columns,
            "granularity": granularity,
        }

    start, end = _resolve_range(date_str=date_str, start_date=start_date, end_date=end_date)
    orders = RakutenApiClient(store_id=store_id).get_orders_detailed(start, end)
    records = build_records(orders, columns, granularity=granularity)
    result = sheet_manager.bitable_bulk_upsert_records(app_token, table_id, records, dry_run=dry_run)
    preview = [{"keys": key_fields, "fields": fields} for fields, key_fields in records[:5]]
    action = "Dry-run finished" if dry_run else "Sync finished"
    return {
        "success": True,
        "message": f"{action}: {len(orders)} orders, {len(records)} rows",
        "store_id": store_id,
        "start_date": start,
        "end_date": end,
        "granularity": granularity,
        "orders_count": len(orders),
        "rows_count": len(records),
        "warnings": {},
        "preview": preview if dry_run else [],
        **result,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--store-id", default="default")
    args = parser.parse_args()
    print(json.dumps(run_rakuten_orders_sync(args.date, args.start_date, args.end_date, args.dry_run, args.inspect, args.store_id), ensure_ascii=False, indent=2))
