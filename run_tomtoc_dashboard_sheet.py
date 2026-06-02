from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import requests

os.environ["LARK_USER_ACCESS_TOKEN"] = ""

from run_dashboard_sheet_export import (
    _amount,
    _age_spu_sales,
    _detail_month_metrics,
    _normalized_keys,
    _number,
    _orders,
    _rows_by_chart,
    _sex_spu_order_rows,
    _text,
)
from run_yearly_dashboard_charts import MONTHS, _build_rows, _records, _store_config
from src.core.config_manager import get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token

SPREADSHEET_TOKEN = get_env("FEISHU_TOMTOC_DASHBOARD_SPREADSHEET_TOKEN", "") or ""
DASHBOARD_SHEET_ID = get_env("FEISHU_TOMTOC_DASHBOARD_SHEET_ID", "") or ""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    resp = requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"{method} {url} HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{method} {url} failed: {data}")
    return data


def _num_to_col(n: int) -> str:
    text = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        text = chr(rem + ord("A")) + text
    return text


def _blank(rows: int = 180, cols: int = 20) -> list[list[Any]]:
    return [["" for _ in range(cols)] for _ in range(rows)]


def _write(sheet_id: str, start_row: int, start_col: int, values: list[list[Any]]) -> None:
    row_count = len(values)
    col_count = max(len(row) for row in values)
    normalized = [row + [""] * (col_count - len(row)) for row in values]
    start = f"{_num_to_col(start_col)}{start_row}"
    end = f"{_num_to_col(start_col + col_count - 1)}{start_row + row_count - 1}"
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/values"
    _request(
        "PUT",
        url,
        json={"valueInputOption": "RAW", "valueRange": {"range": f"{sheet_id}!{start}:{end}", "values": normalized}},
    )


def _read(sheet_id: str, cell_range: str) -> list[list[Any]]:
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/values_batch_get"
    data = _request("GET", url, params={"ranges": f"{sheet_id}!{cell_range}"})
    ranges = (data.get("data") or {}).get("valueRanges") or []
    return (ranges[0].get("values") if ranges else None) or []


def _clear_range(sheet_id: str, start_row: int, start_col: int, rows: int, cols: int) -> None:
    _write(sheet_id, start_row, start_col, _blank(rows=rows, cols=cols))


def _insert_rows(sheet_id: str, before_row: int, count: int) -> None:
    if count <= 0:
        return
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/insert_dimension_range"
    _request(
        "POST",
        url,
        json={
            "dimension": {
                "sheetId": sheet_id,
                "majorDimension": "ROWS",
                "startIndex": before_row - 1,
                "endIndex": before_row - 1 + count,
            },
            "inheritStyle": "BEFORE",
        },
    )


def _grid() -> list[list[Any]]:
    return _read(DASHBOARD_SHEET_ID, "A1:O320")


def _find_row(grid: list[list[Any]], label: str) -> int:
    for index, row in enumerate(grid, 1):
        if any(_text(cell) == label for cell in row):
            return index
    raise RuntimeError(f"Cannot find dashboard row label: {label}")


def _find_row_optional(grid: list[list[Any]], label: str) -> int | None:
    try:
        return _find_row(grid, label)
    except RuntimeError:
        return None


def _find_sex_product_title_row(grid: list[list[Any]]) -> int:
    title_row = _find_row_optional(grid, "性別比率 x 产品")
    if title_row:
        return title_row
    for index, row in enumerate(grid, 1):
        if len(row) >= 3 and _text(row[0]) == "SPU" and _text(row[1]) == "男性" and _text(row[2]) == "女性":
            return max(1, index - 1)
    raise RuntimeError("Cannot find sex product dashboard block")


def _find_age_product_title_row(grid: list[list[Any]]) -> int:
    for label in ("年齢段 x 产品 件数", "年齢段 x 产品 売上"):
        title_row = _find_row_optional(grid, label)
        if title_row:
            return title_row
    raise RuntimeError("Cannot find age product dashboard block")


def _ensure_gap_capacity(start_row: int, next_title_row: int, required_rows: int, gap_rows: int = 2) -> int:
    capacity = next_title_row - start_row - gap_rows
    if required_rows <= capacity:
        return 0
    insert_count = required_rows - capacity
    _insert_rows(DASHBOARD_SHEET_ID, next_title_row - gap_rows, insert_count)
    return insert_count


def _time_bucket_2h(hour: Any) -> str:
    try:
        start = (int(str(hour).strip()) // 2) * 2
    except ValueError:
        return "不明"
    return f"{start:02d}-{start + 1:02d}"


def _spu_map(config: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in _records(config, config.spu_monthly_table):
        fields = item.get("fields") or {}
        spu = _text(fields.get("SPU"))
        if not spu:
            continue
        for key_field in ("SPU", "tag_spu"):
            for key in _normalized_keys(fields.get(key_field)):
                mapping[key] = spu
    return mapping


def _order_spu(fields: dict[str, Any], mapping: dict[str, str]) -> str:
    for key_field in ("システム連携用SKU番号", "SKU管理番号"):
        for key in _normalized_keys(fields.get(key_field)):
            if key in mapping:
                return mapping[key]
            prefix = key.split("-", 1)[0].split("_", 1)[0]
            if prefix in mapping:
                return mapping[prefix]
    sku = _text(fields.get("SKU管理番号") or fields.get("システム連携用SKU番号"))
    return sku.upper() if sku else "不明"


def _spu_monthly_growth_rows(config: Any, top_n: int | None = 10) -> list[list[Any]]:
    source_rows = _spu_monthly_from_orders(config)

    values: list[list[Any]] = [["SPU", "指標", "合計", *MONTHS]]
    sorted_rows = sorted(source_rows, key=lambda item: item["total_units"], reverse=True)
    if top_n is not None:
        sorted_rows = sorted_rows[:top_n]
    for row in sorted_rows:
        values.append([row["spu"], "数量", row["total_units"], *row["units"]])
        values.append([row["spu"], "金額", row["total_amount"], *row["amount"]])
    return values


def _spu_monthly_from_orders(config: Any) -> list[dict[str, Any]]:
    mapping = _spu_map(config)
    by_spu: dict[str, dict[str, Any]] = {}
    for spu in _all_spus(config):
        by_spu[spu] = {
            "spu": spu,
            "units": [0.0 for _ in MONTHS],
            "amount": [0.0 for _ in MONTHS],
            "total_units": 0.0,
            "total_amount": 0.0,
        }
    month_index = {month: index for index, month in enumerate(MONTHS)}
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if month not in month_index:
            continue
        spu = _order_spu(fields, mapping)
        if spu not in by_spu:
            by_spu[spu] = {
                "spu": spu,
                "units": [0.0 for _ in MONTHS],
                "amount": [0.0 for _ in MONTHS],
                "total_units": 0.0,
                "total_amount": 0.0,
            }
        idx = month_index[month]
        units = _number(fields.get("個数"))
        amount = _amount(fields, config)
        by_spu[spu]["units"][idx] += units
        by_spu[spu]["amount"][idx] += amount
        by_spu[spu]["total_units"] += units
        by_spu[spu]["total_amount"] += amount
    return list(by_spu.values())


def _spu_totals(config: Any) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {"units": 0.0, "amount": 0.0})
    for row in _spu_monthly_from_orders(config):
        totals[row["spu"]]["units"] = row["total_units"]
        totals[row["spu"]]["amount"] = row["total_amount"]
    return totals


def _all_spus(config: Any) -> list[str]:
    spus: list[str] = []
    for item in _records(config, config.spu_monthly_table):
        spu = _text((item.get("fields") or {}).get("SPU"))
        if spu:
            spus.append(spu)
    return list(dict.fromkeys(spus))


def _composition_total_values(config: Any) -> list[list[Any]]:
    totals = _spu_totals(config)
    values: list[list[Any]] = []
    for spu in sorted(_all_spus(config), key=lambda item: totals.get(item, {}).get("units", 0), reverse=True):
        metric = totals.get(spu, {})
        values.append([spu, metric.get("units", 0), metric.get("amount", 0)])
    return values


def _spu_age_sex_rows(config: Any, top_n: int = 12) -> list[list[Any]]:
    mapping = _spu_map(config)
    metrics: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "units": 0.0, "orders": 0.0})
    spu_totals: dict[str, float] = defaultdict(float)
    seen_orders: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        spu = _order_spu(fields, mapping)
        age = _text(fields.get("年齢段")) or "不明"
        sex = _text(fields.get("性別")) or "不明"
        key = (spu, age, sex)
        amount = _amount(fields, config)
        units = _number(fields.get("個数"))
        metrics[key]["sales"] += amount
        metrics[key]["units"] += units
        spu_totals[spu] += amount
        order_no = _text(fields.get("注文番号"))
        if order_no:
            seen_orders[key].add(order_no)
    for key, orders in seen_orders.items():
        metrics[key]["orders"] = float(len(orders))

    top_spus = {spu for spu, _ in sorted(spu_totals.items(), key=lambda item: item[1], reverse=True)[:top_n]}
    rows = [["SPU", "年齢段", "性別", "金額", "数量", "件数"]]
    def sort_key(item: tuple[tuple[str, str, str], dict[str, float]]) -> tuple[int, str, str, str]:
        (spu, age, sex), _ = item
        return (0 if spu in top_spus else 1, spu, age, sex)

    for (spu, age, sex), value in sorted(metrics.items(), key=sort_key):
        if spu not in top_spus:
            continue
        rows.append([spu, age, sex, value["sales"], value["units"], value["orders"]])
    return rows


def _monthly_values(config: Any) -> list[list[Any]]:
    detail_month = _detail_month_metrics(config)
    values = [["月", "売上実績", "件数", "数量", "客単価", "件単価"]]
    for month in [f"{i}月" for i in range(1, 13)]:
        detail = detail_month.get(month, {})
        sales = detail.get("sales", 0)
        orders = detail.get("orders", 0)
        units = detail.get("units", 0)
        values.append([month, sales, orders, units, sales / orders if orders else "", sales / units if units else ""])
    return values


def _spu_monthly_values(config: Any) -> list[list[Any]]:
    return _spu_monthly_growth_rows(config, top_n=None)[1:]


def _time_bucket_values(grouped: dict[str, list[dict[str, Any]]]) -> list[list[Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "units": 0.0, "orders": 0.0})
    for row in grouped.get("購入時間帯 売上", []):
        bucket = _time_bucket_2h(row.get("月"))
        buckets[bucket]["sales"] += _number(row.get("金額"))
        buckets[bucket]["units"] += _number(row.get("数量"))
        buckets[bucket]["orders"] += _orders(row.get("補足"))
    values = [["時間帯", "金額", "数量", "注文数"]]
    for hour in range(0, 24, 2):
        bucket = f"{hour:02d}-{hour + 1:02d}"
        metrics = buckets.get(bucket, {})
        values.append([bucket, metrics.get("sales", 0), metrics.get("units", 0), metrics.get("orders", 0)])
    if "不明" in buckets:
        metrics = buckets["不明"]
        values.append(["不明", metrics["sales"], metrics["units"], metrics["orders"]])
    return values


def _age_sex_values(config: Any) -> list[list[Any]]:
    return _spu_age_sex_rows(config)


def _age_product_values(config: Any) -> list[list[Any]]:
    ages, products, pivot = _age_spu_order_counts(config)
    values: list[list[Any]] = [["年齢段", *products, "合計"]]
    for age in ages:
        counts = [pivot.get(age, {}).get(product, 0) for product in products]
        values.append([age, *counts, sum(counts)])
    return values


def _age_spu_order_counts(config: Any, top_n: int = 10) -> tuple[list[str], list[str], dict[str, dict[str, float]]]:
    mapping = _spu_map(config)
    spu_totals: dict[str, float] = defaultdict(float)
    order_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    fallback_counts: dict[tuple[str, str], float] = defaultdict(float)
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        spu = _order_spu(fields, mapping)
        age = _text(fields.get("年齢段")) or "不明"
        order_no = _text(fields.get("注文番号"))
        key = (age, spu)
        if order_no:
            order_sets[key].add(order_no)
        else:
            fallback_counts[key] += 1
        spu_totals[spu] += _amount(fields, config)
    products = [spu for spu, _ in sorted(spu_totals.items(), key=lambda item: item[1], reverse=True)[:top_n]]
    pivot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (age, spu), orders in order_sets.items():
        if spu in products:
            pivot[age][spu] += len(orders)
    for (age, spu), count in fallback_counts.items():
        if spu in products:
            pivot[age][spu] += count
    ages = sorted(pivot, key=lambda value: (value == "不明", value))
    return ages, products, pivot


def _sex_product_values(config: Any) -> list[list[Any]]:
    return _sex_spu_order_rows(config)


def _tomtoc_dashboard_values() -> list[list[Any]]:
    config = _store_config("store2")
    rows = _build_rows(config)
    grouped = _rows_by_chart(rows)
    detail_month = _detail_month_metrics(config)
    values = _blank(rows=220, cols=28)

    values[0][:8] = ["tomtoc 仪表盘", "渠道", "楽天", "更新方式", "脚本刷新", "Yahoo区域", "预留", "2026"]
    values[2][:10] = ["楽天区域", "", "", "", "", "", "", "", "", ""]
    values[2][23:28] = ["Yahoo区域（预留）", "", "", "", ""]
    values[3][23:28] = ["之后同步 tomtoc Yahoo 数据时使用", "", "", "", ""]

    values[4][:6] = ["月次売上", "", "", "", "Chart", "line/bar"]
    values[5][:6] = ["月", "売上実績", "件数", "数量", "客単価", "件単価"]
    for idx, month in enumerate([f"{i}月" for i in range(1, 13)], 6):
        detail = detail_month.get(month, {})
        sales = detail.get("sales", 0)
        orders = detail.get("orders", 0)
        units = detail.get("units", 0)
        values[idx][:6] = [
            month,
            sales,
            orders,
            units,
            sales / orders if orders else "",
            sales / units if units else "",
        ]

    values[4][7:22] = ["SPU 月次推移（数量/金額）", "", "", "", "", "", "", "", "", "", "", "", "", "", "Chart: line"]
    for row_index, row in enumerate(_spu_monthly_growth_rows(config), 5):
        values[row_index][7:22] = row

    values[20][:5] = ["購入時間帯 売上", "", "", "", "Chart: bar/line"]
    values[21][:5] = ["時", "金額", "数量", "注文数", ""]
    hour_rows = grouped.get("購入時間帯 売上", [])
    for idx, row in enumerate(sorted(hour_rows, key=lambda item: str(item.get("月") or "")), 22):
        values[idx][:5] = [row.get("月"), row.get("金額"), row.get("数量"), _orders(row.get("補足")), ""]

    values[60][:8] = ["SPU x 年齢段 x 性別", "", "", "", "", "", "", "Chart: pivot/stacked bar"]
    for row_index, row in enumerate(_spu_age_sex_rows(config), 61):
        values[row_index][:6] = row

    values[6][23:28] = ["Yahoo 月次売上", "売上", "件数", "数量", "備考"]
    for idx, month in enumerate([f"{i}月" for i in range(1, 13)], 7):
        values[idx][23:28] = [month, "", "", "", "预留"]
    values[22][23:28] = ["Yahoo SPU 月次推移", "SPU", "月", "数量", "金額"]
    values[38][23:28] = ["Yahoo 年齢段/性別/時間帯", "分類", "値", "金額", "備考"]

    return values


def _write_tomtoc_dashboard_blocks() -> dict[str, int]:
    config = _store_config("store2")
    grouped = _rows_by_chart(_build_rows(config))
    blocks = {
        "monthly": (6, 1, 13, 6, _monthly_values(config)),
        "composition_totals": (23, 2, 0, 0, _composition_total_values(config)),
        "spu_monthly": (41, 1, 21, 15, _spu_monthly_values(config)),
        "time_bucket": (65, 1, 22, 4, _time_bucket_values(grouped)),
        "age_sex": (92, 1, 120, 6, _age_sex_values(config)),
        "age_product": (215, 1, 26, 14, [["年齢段 x 产品 売上"], *_age_product_values(config)]),
        "sex_product": (245, 1, 18, 7, [["性別比率 x 产品"], *_sex_product_values(config)]),
    }
    for _, (row, col, clear_rows, clear_cols, values) in blocks.items():
        if clear_rows and clear_cols:
            _clear_range(DASHBOARD_SHEET_ID, row, col, clear_rows, clear_cols)
        _write(DASHBOARD_SHEET_ID, row, col, values)
    return {name: len(values) for name, (_, _, _, _, values) in blocks.items()}


def _write_tomtoc_dashboard_blocks() -> dict[str, int]:
    config = _store_config("store2")
    grouped = _rows_by_chart(_build_rows(config))
    monthly_values = _monthly_values(config)
    composition_values = _composition_total_values(config)
    spu_monthly_values = _spu_monthly_values(config)
    time_values = _time_bucket_values(grouped)
    age_product_values = _age_product_values(config)
    sex_product_values = _sex_product_values(config)
    age_sex_values = _age_sex_values(config)

    grid = _grid()
    comp_title = _find_row(grid, "月累计构成比")
    spu_title = _find_row(grid, "SPU 月次推移（数量/金額）")
    comp_start = comp_title + 2
    inserted_comp = _ensure_gap_capacity(comp_start, spu_title, len(composition_values), gap_rows=2)

    if inserted_comp:
        grid = _grid()
        spu_title = _find_row(grid, "SPU 月次推移（数量/金額）")
    monthly_start = spu_title + 2
    time_title = _find_row(grid, "購入時間帯 売上")
    inserted_spu = _ensure_gap_capacity(monthly_start, time_title, len(spu_monthly_values), gap_rows=2)

    if inserted_spu:
        grid = _grid()
        time_title = _find_row(grid, "購入時間帯 売上")

    age_product_title = _find_age_product_title_row(grid)
    sex_product_title = _find_sex_product_title_row(grid)
    age_sex_title = _find_row(grid, "SPU x 年齢段 x 性別")

    blocks = {
        "monthly": (6, 1, 13, 6, monthly_values),
        "composition_totals": (comp_start, 1, max(len(composition_values), spu_title - comp_start - 2), 3, composition_values),
        "spu_monthly": (monthly_start, 1, max(len(spu_monthly_values), time_title - monthly_start - 2), 15, spu_monthly_values),
        "time_bucket": (time_title + 1, 1, 13, 4, time_values),
        "age_product": (age_product_title + 1, 1, 20, 14, age_product_values),
        "sex_product": (sex_product_title + 1, 1, 12, 7, sex_product_values),
        "age_sex": (age_sex_title + 1, 1, 140, 6, age_sex_values),
    }
    _write(DASHBOARD_SHEET_ID, age_product_title, 1, [["年齢段 x 产品 件数"]])
    for _, (row, col, clear_rows, clear_cols, values) in blocks.items():
        if clear_rows and clear_cols:
            _clear_range(DASHBOARD_SHEET_ID, row, col, clear_rows, clear_cols)
        _write(DASHBOARD_SHEET_ID, row, col, values)
    result = {name: len(values) for name, (_, _, _, _, values) in blocks.items()}
    result["inserted_rows"] = inserted_comp + inserted_spu
    return result


def run_tomtoc_dashboard_sheet() -> dict[str, Any]:
    rows_by_block = _write_tomtoc_dashboard_blocks()
    return {
        "success": True,
        "spreadsheet_token": SPREADSHEET_TOKEN,
        "sheet_id": DASHBOARD_SHEET_ID,
        "rows_written": sum(rows_by_block.values()),
        "rows_by_block": rows_by_block,
        "message": "tomtoc dashboard sheet updated",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_tomtoc_dashboard_sheet(), ensure_ascii=False, indent=2))
