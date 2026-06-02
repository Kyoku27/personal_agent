from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable

JST = timezone(timedelta(hours=9))
SKU_HEADER = "SKU"
TOTAL_LABEL = "Total"


@dataclass(frozen=True)
class WeekColumn:
    col_index: int
    label: str
    start: date
    end: date


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _num_to_col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(r + ord("A")) + s
    return s


def _parse_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _default_start(today: date) -> date:
    return date(today.year, 3, 1)


def _resolve_range(start_date: str | None, end_date: str | None) -> tuple[date, date]:
    today = datetime.now(JST).date()
    start = _parse_date(start_date, "start_date") if start_date else _default_start(today)
    end = _parse_date(end_date, "end_date") if end_date else today
    if start > end:
        raise ValueError("start_date cannot be after end_date")
    return start, end


def _spreadsheet_token() -> str:
    direct = get_env("FEISHU_RAKUTEN_TOMTOC_WEEKLY_SHEET_TOKEN", "") or ""
    if direct:
        return direct
    node_token = get_env("FEISHU_RAKUTEN_TOMTOC_WEEKLY_WIKI_NODE_TOKEN", "") or ""
    if not node_token:
        raise RuntimeError("FEISHU_RAKUTEN_TOMTOC_WEEKLY_WIKI_NODE_TOKEN is not configured")
    return resolve_wiki_to_bitable(node_token=node_token)


def _sheet_id() -> str:
    sheet_id = get_env("FEISHU_RAKUTEN_TOMTOC_WEEKLY_SHEET_ID", "") or ""
    if not sheet_id:
        raise RuntimeError("FEISHU_RAKUTEN_TOMTOC_WEEKLY_SHEET_ID is not configured")
    return sheet_id


def _batch_get(spreadsheet_token: str, ranges: list[str]) -> list[dict[str, Any]]:
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get"
    resp = requests.get(url, headers=_headers(), params={"ranges": ranges}, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Lark Sheets HTTP {resp.status_code} batch_get: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark Sheets batch_get failed: {data}")
    return (data.get("data") or {}).get("valueRanges") or []


def _batch_update(spreadsheet_token: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
    if not updates:
        return {"updated_cells": 0}
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_update"
    resp = requests.post(
        url,
        headers=_headers(),
        json={"valueInputOption": "RAW", "valueRanges": updates},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Lark Sheets HTTP {resp.status_code} batch_update: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark Sheets batch_update failed: {data}")
    return data


def _get_grid(spreadsheet_token: str, sheet_id: str) -> list[list[Any]]:
    ranges = _batch_get(spreadsheet_token, [f"{sheet_id}!A1:AZ500"])
    return (ranges[0].get("values") if ranges else None) or []


def _month_num(text: str) -> int | None:
    key = text.strip().lower().rstrip(".")
    aliases = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    return aliases.get(key)


def _parse_week_label(label: str, year: int) -> tuple[date, date] | None:
    match = re.match(r"^\s*([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2})\s*$", label)
    if not match:
        return None
    month = _month_num(match.group(1))
    if not month:
        return None
    start_day = int(match.group(2))
    end_day = int(match.group(3))
    start = date(year, month, start_day)
    end_month = month if end_day >= start_day else (month % 12) + 1
    end_year = year + 1 if month == 12 and end_month == 1 else year
    end = date(end_year, end_month, end_day)
    return start, end


def _find_sku_header_row(grid: list[list[Any]]) -> int:
    for idx, row in enumerate(grid):
        first = str(row[0]).strip() if row else ""
        if first == SKU_HEADER:
            return idx
    raise RuntimeError("Could not find SKU header row in weekly sheet")


def _find_total_row(grid: list[list[Any]]) -> int | None:
    for idx, row in enumerate(grid):
        first = str(row[0]).strip() if row else ""
        if first == TOTAL_LABEL:
            return idx
    return None


def _week_columns(header_row: list[Any], year: int, start: date, end: date) -> list[WeekColumn]:
    columns: list[WeekColumn] = []
    for idx, value in enumerate(header_row):
        label = str(value or "").strip()
        parsed = _parse_week_label(label, year)
        if not parsed:
            continue
        week_start, week_end = parsed
        if week_end < start or week_start > end:
            continue
        columns.append(WeekColumn(idx + 1, label, max(week_start, start), min(week_end, end)))
    return columns


def _normalize_sku(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _sku_rows(grid: list[list[Any]], sku_header_row: int) -> dict[str, int]:
    rows: dict[str, int] = {}
    for idx in range(sku_header_row + 1, len(grid)):
        row = grid[idx]
        sku = str(row[0]).strip() if row else ""
        if sku:
            rows[_normalize_sku(sku)] = idx + 1
    return rows


def _item_sku(item: dict[str, Any]) -> str:
    for key in ("systemSku", "variantId", "itemNumber", "manageNumber"):
        sku = _normalize_sku(item.get(key))
        if sku:
            return sku
    return ""


def _order_day(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).astimezone(JST).date()
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text)
        return (dt if dt.tzinfo else dt.replace(tzinfo=JST)).astimezone(JST).date()
    except ValueError:
        return None


def _aggregate_units(orders: list[dict[str, Any]], weeks: list[WeekColumn]) -> tuple[dict[tuple[str, int], int], set[str]]:
    totals: dict[tuple[str, int], int] = defaultdict(int)
    all_skus: set[str] = set()
    for order in orders:
        day = _order_day(order.get("orderDatetime"))
        if not day:
            continue
        week = next((item for item in weeks if item.start <= day <= item.end), None)
        if not week:
            continue
        for item in order.get("items") or []:
            sku = _item_sku(item)
            if not sku:
                continue
            all_skus.add(sku)
            try:
                units = int(item.get("units") or 0)
            except (TypeError, ValueError):
                units = 0
            totals[(sku, week.col_index)] += units
    return totals, all_skus


def run_tomtoc_weekly_sheet_sync(
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    inspect: bool = False,
) -> dict[str, Any]:
    start, end = _resolve_range(start_date, end_date)
    spreadsheet_token = _spreadsheet_token()
    sheet_id = _sheet_id()
    grid = _get_grid(spreadsheet_token, sheet_id)
    sku_header_idx = _find_sku_header_row(grid)
    header_row = grid[sku_header_idx]
    weeks = _week_columns(header_row, start.year, start, end)
    rows_by_sku = _sku_rows(grid, sku_header_idx)
    total_row = _find_total_row(grid)

    if inspect:
        return {
            "success": True,
            "message": f"Loaded tomtoc weekly sheet: {len(rows_by_sku)} SKU rows, {len(weeks)} week columns in range",
            "sheet_id": sheet_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "sku_count": len(rows_by_sku),
            "week_columns": [{"label": w.label, "start": w.start.isoformat(), "end": w.end.isoformat(), "column": _num_to_col(w.col_index)} for w in weeks],
            "preview": [],
        }

    if not weeks:
        return {
            "success": False,
            "message": "No weekly columns matched the requested date range",
            "sheet_id": sheet_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }

    orders = RakutenApiClient(store_id="store2").get_orders_detailed(start.isoformat(), end.isoformat())
    aggregated, order_skus = _aggregate_units(orders, weeks)
    missing_skus = sorted(sku for sku in order_skus if sku not in rows_by_sku)
    updates: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []

    for (sku, col_index), units in sorted(aggregated.items()):
        row_no = rows_by_sku.get(sku)
        if not row_no:
            continue
        col = _num_to_col(col_index)
        updates.append({"range": f"{sheet_id}!{col}{row_no}:{col}{row_no}", "values": [[units]]})
        if len(preview) < 20:
            week = next((item for item in weeks if item.col_index == col_index), None)
            preview.append({"sku": sku, "week": week.label if week else col, "cell": f"{col}{row_no}", "units": units})

    if total_row:
        for week in weeks:
            col = _num_to_col(week.col_index)
            col_total = sum(value for (sku, idx), value in aggregated.items() if idx == week.col_index and sku in rows_by_sku)
            updates.append({"range": f"{sheet_id}!{col}{total_row + 1}:{col}{total_row + 1}", "values": [[col_total]]})

    if not dry_run:
        _batch_update(spreadsheet_token, updates)

    action = "Dry-run finished" if dry_run else "Weekly sheet updated"
    return {
        "success": True,
        "message": f"{action}: {len(orders)} orders, {len(updates)} cells planned",
        "sheet_id": sheet_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "orders_count": len(orders),
        "matched_sku_count": len(order_skus) - len(missing_skus),
        "missing_sku_count": len(missing_skus),
        "updated_cells": 0 if dry_run else len(updates),
        "planned_updates": len(updates),
        "dry_run": dry_run,
        "week_columns": [{"label": w.label, "start": w.start.isoformat(), "end": w.end.isoformat(), "column": _num_to_col(w.col_index)} for w in weeks],
        "missing_skus": missing_skus[:50],
        "preview": preview,
    }
