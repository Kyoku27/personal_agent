from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import os
from typing import Any

import requests

os.environ["LARK_USER_ACCESS_TOKEN"] = ""

from run_yearly_dashboard_charts import _amount, _build_rows, _records as _raw_records, _store_config, _text
from src.core.config_manager import get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token

SPREADSHEET_TOKEN = get_env("FEISHU_DASHBOARD_SPREADSHEET_TOKEN", "") or ""
STORE_SHEETS = {"default": "EZLIFE", "store2": "tomtoc"}
BASE_LINK_SHEET = "Base_Link"
SKU_MONTHLY_TABLES = {
    "default": "tblzhxONfM6R9v1H",
    "store2": "tblGXjllBaIOTRGq",
}
BRAND_ORDER = ["HRP", "CZUR", "MOFT", "Genki", "HiDock", "tomtoc", "不明"]
MONTHS = [f"{i}月" for i in range(1, 13)]
_RECORD_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _records(config: Any, table_id: str) -> list[dict[str, Any]]:
    key = (getattr(config, "app_token", ""), table_id)
    if key not in _RECORD_CACHE:
        _RECORD_CACHE[key] = _raw_records(config, table_id)
    return _RECORD_CACHE[key]


def _dashboard_month(detail_month: dict[str, dict[str, float]]) -> str:
    current = f"{datetime.now().month}月"
    if detail_month.get(current, {}).get("sales"):
        return current
    for month in reversed(MONTHS):
        if detail_month.get(month, {}).get("sales"):
            return month
    return current


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


def _list_sheets() -> dict[str, str]:
    url = f"{FEISHU_BASE_URL}/sheets/v3/spreadsheets/{SPREADSHEET_TOKEN}/sheets/query"
    data = _request("GET", url)
    return {
        item["title"]: item["sheet_id"]
        for item in (data.get("data") or {}).get("sheets") or []
        if item.get("title") and item.get("sheet_id")
    }


def _ensure_sheet(title: str, index: int) -> str:
    sheets = _list_sheets()
    if title in sheets:
        return sheets[title]
    url = f"{FEISHU_BASE_URL}/sheets/v2/spreadsheets/{SPREADSHEET_TOKEN}/sheets_batch_update"
    data = _request(
        "POST",
        url,
        json={"requests": [{"addSheet": {"properties": {"title": title, "index": index}}}]},
    )
    return str(((data.get("data") or {}).get("replies") or [{}])[0].get("addSheet", {}).get("properties", {}).get("sheetId"))


def _blank(rows: int = 180, cols: int = 20) -> list[list[str]]:
    return [["" for _ in range(cols)] for _ in range(rows)]


def _write(sheet_id: str, start_row: int, start_col: int, values: list[list[Any]]) -> None:
    if not values:
        return
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


def _write_ezlife_dashboard_data(sheet_id: str) -> dict[str, int]:
    blocks = _ezlife_dashboard_blocks()
    for _, (row, col, values) in blocks.items():
        _write(sheet_id, row, col, values)
    return {name: len(values) for name, (_, _, values) in blocks.items()}


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _orders(text: Any) -> int:
    if not text:
        return 0
    value = str(text)
    if value.startswith("orders="):
        try:
            return int(value.split("=", 1)[1])
        except ValueError:
            return 0
    return 0


def _list_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = _text(item)
                if text:
                    result.append(text)
            elif item not in (None, ""):
                result.append(str(item).strip())
        return [item for item in result if item]
    text = _text(value).strip()
    return [text] if text else []


def _normalized_keys(value: Any) -> list[str]:
    keys: list[str] = []
    for text in _list_values(value):
        stripped = text.strip()
        if not stripped:
            continue
        keys.append(stripped)
        keys.append(stripped.upper())
        keys.append(stripped.lower())
        keys.append(stripped.replace("_", "-"))
        keys.append(stripped.replace("-", "_"))
    return list(dict.fromkeys(keys))


def _brand_from_name(name: str) -> str:
    upper = name.upper()
    for brand in ("HRP", "CZUR", "MOFT", "GENKI", "HIDOCK", "TOMTOC"):
        if brand in upper:
            return _normalize_brand("HiDock" if brand == "HIDOCK" else brand.title() if brand in ("GENKI", "TOMTOC") else brand)
    return "不明"


def _normalize_brand(brand: str) -> str:
    key = (brand or "").strip()
    aliases = {
        "hidock": "HiDock",
        "hiDock": "HiDock",
        "Hidock": "HiDock",
        "genki": "Genki",
        "Tomtoc": "tomtoc",
        "TOMTOC": "tomtoc",
    }
    return aliases.get(key, key or "不明")


def _normalize_age(age: str) -> str:
    key = (age or "").strip()
    if key.endswith("代"):
        try:
            decade = int(key[:-1])
            return key if 10 <= decade <= 90 else "不明"
        except ValueError:
            return key
    return key or "不明"


def _normalize_sex(sex: str) -> str:
    key = (sex or "").strip()
    aliases = {
        "0": "不明",
        "1": "男性",
        "2": "女性",
        "M": "男性",
        "F": "女性",
        "male": "男性",
        "female": "女性",
        "男": "男性",
        "女": "女性",
    }
    return aliases.get(key, key or "不明")


def _brand_map(config: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    sku_table = SKU_MONTHLY_TABLES.get(config.store_id)
    if sku_table:
        for item in _records(config, sku_table):
            fields = item.get("fields") or {}
            brand = _normalize_brand(_text(fields.get("Tag2HRP") or fields.get("ダッシュボード用TAG")) or _brand_from_name(_text(fields.get("商品名"))))
            if brand == "不明":
                brand = default_brand
            for key_field in ("商品名", "Tag"):
                for key in _normalized_keys(fields.get(key_field)):
                    mapping[key] = brand
    for item in _records(config, config.spu_monthly_table):
        fields = item.get("fields") or {}
        brand = _normalize_brand(_text(fields.get("Tag2HRP") or fields.get("ダッシュボード用TAG")) or _brand_from_name(_text(fields.get("SPU"))))
        if brand == "不明":
            brand = default_brand
        for key_field in ("SPU", "tag_spu"):
            for key in _normalized_keys(fields.get(key_field)):
                mapping[key] = brand
    return mapping


def _order_brand(fields: dict[str, Any], mapping: dict[str, str], default_brand: str = "不明") -> str:
    for key_field in ("システム連携用SKU番号", "SKU管理番号"):
        for key in _normalized_keys(fields.get(key_field)):
            if key in mapping:
                return _normalize_brand(mapping[key])
            prefix = key.split("-", 1)[0].split("_", 1)[0]
            if prefix in mapping:
                return _normalize_brand(mapping[prefix])
    brand = _normalize_brand(_brand_from_name(_text(fields.get("商品名"))))
    return default_brand if brand == "不明" else brand


def _detail_month_metrics(config: Any) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "units": 0.0, "orders": 0.0})
    seen_orders: dict[str, set[str]] = defaultdict(set)
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if not month:
            continue
        metrics[month]["sales"] += _amount(fields, config)
        metrics[month]["units"] += _number(fields.get("個数"))
        order_no = _text(fields.get("注文番号"))
        if order_no:
            seen_orders[month].add(order_no)
    for month, order_numbers in seen_orders.items():
        metrics[month]["orders"] = float(len(order_numbers))
    return metrics


def _target_sales_by_month(config: Any) -> dict[str, float]:
    if not getattr(config, "annual_target_table", None):
        return {}
    targets: dict[str, float] = defaultdict(float)
    for item in _records(config, config.annual_target_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if month:
            targets[month] = _number(fields.get("売上目標"))
    return targets


def _rms_sales_by_month(config: Any) -> dict[str, float]:
    sales: dict[str, float] = defaultdict(float)
    if not getattr(config, "annual_target_table", None):
        return sales
    for item in _records(config, config.annual_target_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if not month:
            continue
        sales[month] = (
            _number(fields.get("RMS売上"))
            or _number(fields.get("売上_実績_表示"))
            or _number(fields.get("売上_実績_折后"))
        )
    return sales


def _brand_month_sales_from_annual(config: Any) -> tuple[list[str], dict[str, dict[str, float]]]:
    brands = ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]
    pivot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if not getattr(config, "annual_target_table", None):
        return brands, pivot
    for item in _records(config, config.annual_target_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if not month:
            continue
        for brand in brands:
            pivot[month][brand] = (
                _number(fields.get(f"{brand}_実績_表示"))
                or _number(fields.get(f"{brand}_実績_折后"))
            )
    return brands, pivot


def _brand_spu_top_units(config: Any) -> list[dict[str, Any]]:
    totals_by_brand: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    for item in _records(config, config.spu_monthly_table):
        fields = item.get("fields") or {}
        spu = _text(fields.get("SPU"))
        if not spu:
            continue
        brand = _normalize_brand(_text(fields.get("Tag2HRP") or fields.get("ダッシュボード用TAG")) or _brand_from_name(spu))
        if brand == "不明":
            brand = default_brand
        amount = sum(_number(fields.get(f"{month}_金額")) for month in [f"{i}月" for i in range(1, 13)])
        units = sum(_number(fields.get(f"{month}合計")) for month in [f"{i}月" for i in range(1, 13)])
        if units or amount:
            totals_by_brand[brand].append((spu, units, amount))
    rows: list[dict[str, Any]] = []
    for brand in sorted(totals_by_brand):
        for rank, (spu, units, amount) in enumerate(sorted(totals_by_brand[brand], key=lambda item: item[1], reverse=True)[:10], 1):
            rows.append({"ブランド": brand, "順位": rank, "SPU": spu, "数量": units, "金額": amount})
    return rows


def _brand_spu_monthly_top_amount(config: Any) -> list[dict[str, Any]]:
    detail_rows = _brand_spu_monthly_top_amount_from_orders(config)
    if detail_rows:
        return detail_rows
    rows: list[dict[str, Any]] = []
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    for month in MONTHS:
        totals_by_brand: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for item in _records(config, config.spu_monthly_table):
            fields = item.get("fields") or {}
            spu = _text(fields.get("SPU"))
            if not spu:
                continue
            brand = _normalize_brand(_text(fields.get("Tag2HRP") or fields.get("ダッシュボード用TAG")) or _brand_from_name(spu))
            if brand == "不明":
                brand = default_brand
            amount = _number(fields.get(f"{month}_金額"))
            units = _number(fields.get(f"{month}合計"))
            if units or amount:
                totals_by_brand[brand].append((spu, units, amount))
        for brand in sorted(totals_by_brand):
            for rank, (spu, units, amount) in enumerate(sorted(totals_by_brand[brand], key=lambda item: item[2], reverse=True)[:10], 1):
                rows.append({"月": month, "ブランド": brand, "順位": rank, "SPU": spu, "数量": units, "金額": amount})
    return rows


def _brand_spu_monthly_top_amount_from_orders(config: Any) -> list[dict[str, Any]]:
    mapping = _brand_map(config)
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    totals: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: {"units": 0.0, "amount": 0.0})
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if not month:
            continue
        spu = _order_spu(fields, _spu_map(config))
        brand = _order_brand(fields, mapping, default_brand=default_brand)
        key = (month, brand, spu)
        totals[key]["units"] += _number(fields.get("個数"))
        totals[key]["amount"] += _amount(fields, config)

    by_month_brand: dict[tuple[str, str], list[tuple[str, float, float]]] = defaultdict(list)
    for (month, brand, spu), values in totals.items():
        if values["units"] or values["amount"]:
            by_month_brand[(month, brand)].append((spu, values["units"], values["amount"]))

    rows: list[dict[str, Any]] = []
    for month in MONTHS:
        brands = sorted({brand for item_month, brand in by_month_brand if item_month == month})
        for brand in brands:
            ranked = sorted(by_month_brand[(month, brand)], key=lambda item: item[2], reverse=True)[:10]
            for rank, (spu, units, amount) in enumerate(ranked, 1):
                rows.append({"月": month, "ブランド": brand, "順位": rank, "SPU": spu, "数量": units, "金額": amount})
    return rows


def _brand_spu_top_amount_for_month(config: Any, month: str) -> list[dict[str, Any]]:
    return [row for row in _brand_spu_monthly_top_amount(config) if row.get("月") == month]


def _brand_spu_top_blocks_for_month(config: Any, month: str) -> list[list[Any]]:
    rows = _brand_spu_top_amount_for_month(config, month)
    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_brand[str(row.get("ブランド") or "不明")].append(row)

    values: list[list[Any]] = []
    brand_order = [brand for brand in BRAND_ORDER if brand in by_brand]
    brand_order.extend(sorted(set(by_brand) - set(brand_order)))
    for brand in brand_order:
        values.append([f"{brand} TOP10", "", "", "", "", ""])
        values.append(["順位", "SPU", "数量", "金額", "月", "ブランド"])
        for row in sorted(by_brand[brand], key=lambda item: _number(item.get("順位"))):
            values.append([row.get("順位"), row.get("SPU"), row.get("数量"), row.get("金額"), row.get("月"), row.get("ブランド")])
        values.append(["", "", "", "", "", ""])
    return values


def _brand_month_actual_sales(config: Any) -> tuple[list[str], dict[str, dict[str, float]]]:
    mapping = _brand_map(config)
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    pivot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        month = _text(fields.get("月別"))
        if not month:
            continue
        brand = _order_brand(fields, mapping, default_brand=default_brand)
        pivot[month][brand] += _amount(fields, config)
    brands = [brand for brand in BRAND_ORDER if any(brand in pivot[month] for month in MONTHS)]
    brands.extend(sorted({brand for month in MONTHS for brand in pivot[month]} - set(brands)))
    return brands, pivot


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


def _top_spus_from_orders(config: Any, top_n: int = 10) -> list[str]:
    mapping = _spu_map(config)
    totals: dict[str, float] = defaultdict(float)
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        totals[_order_spu(fields, mapping)] += _amount(fields, config)
    return [spu for spu, _ in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top_n]]


def _age_spu_sales(config: Any, top_n: int = 10) -> tuple[list[str], list[str], dict[str, dict[str, float]]]:
    mapping = _spu_map(config)
    top_spus = _top_spus_from_orders(config, top_n=top_n)
    pivot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        spu = _order_spu(fields, mapping)
        if spu not in top_spus:
            continue
        age = _normalize_age(_text(fields.get("年齢段")))
        pivot[age][spu] += _amount(fields, config)
    ages = sorted(pivot, key=lambda value: (value == "不明", value))
    return ages, top_spus, pivot


def _sex_spu_order_rows(config: Any, top_n: int = 10) -> list[list[Any]]:
    mapping = _spu_map(config)
    top_spus = _top_spus_from_orders(config, top_n=top_n)
    order_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    fallback_counts: dict[tuple[str, str], float] = defaultdict(float)
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        spu = _order_spu(fields, mapping)
        if spu not in top_spus:
            continue
        sex = _normalize_sex(_text(fields.get("性別")))
        order_no = _text(fields.get("注文番号"))
        key = (spu, sex)
        if order_no:
            order_sets[key].add(order_no)
        else:
            fallback_counts[key] += 1
    rows: list[list[Any]] = [["SPU", "男性", "女性", "不明", "合計", "男性比率", "女性比率"]]
    for spu in top_spus:
        male = len(order_sets.get((spu, "男性"), set())) + fallback_counts.get((spu, "男性"), 0)
        female = len(order_sets.get((spu, "女性"), set())) + fallback_counts.get((spu, "女性"), 0)
        unknown = sum(
            len(order_sets.get((spu, sex), set())) + fallback_counts.get((spu, sex), 0)
            for _, sex in order_sets.keys() | fallback_counts.keys()
            if sex not in {"男性", "女性"}
        )
        total = male + female + unknown
        rows.append([spu, male, female, unknown, total, male / total if total else "", female / total if total else ""])
    return rows


def _age_brand_sales(config: Any) -> tuple[list[str], list[str], dict[str, dict[str, float]]]:
    mapping = _brand_map(config)
    default_brand = "tomtoc" if getattr(config, "label", "") == "tomtoc" else "不明"
    pivot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in _records(config, config.order_detail_table):
        fields = item.get("fields") or {}
        age = _normalize_age(_text(fields.get("年齢段")))
        brand = _order_brand(fields, mapping, default_brand=default_brand)
        pivot[age][brand] += _amount(fields, config)
    ages = sorted(pivot, key=lambda value: (value == "不明", value))
    brands = [brand for brand in BRAND_ORDER if any(brand in pivot[age] for age in ages)]
    brands.extend(sorted({brand for age in ages for brand in pivot[age]} - set(brands)))
    return ages, brands, pivot


def _rows_by_chart(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("グラフ") or "")].append(row)
    return grouped


def _store_sheet_values(store_id: str) -> list[list[Any]]:
    config = _store_config(store_id)
    rows = _build_rows(config)
    grouped = _rows_by_chart(rows)
    detail_month = _detail_month_metrics(config)
    values = _blank(rows=260, cols=24)
    values[0][:4] = [f"{config.label} Dashboard DB", "generated_by", "Codex", "2026-05-28"]

    values[2][:7] = ["月次売上", "", "", "", "", "Chart", "line/bar"]
    monthly = grouped.get("月次 売上 vs 目標") or grouped.get("月次 売上") or []
    by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for row in monthly:
        month = str(row.get("月") or "")
        metric = str(row.get("指標") or "")
        by_month[month][metric] = _number(row.get("金額"))
        by_month[month]["数量"] = by_month[month].get("数量", 0.0) + _number(row.get("数量"))
        if row.get("達成率") not in (None, ""):
            by_month["__rate__"][month] = _number(row.get("達成率"))
    values[3][:6] = ["月", "売上実績", "売上目標", "達成率", "件数", "数量"]
    for idx, month in enumerate([f"{i}月" for i in range(1, 13)], 4):
        detail = detail_month.get(month, {})
        values[idx][:5] = [
            month,
            by_month.get(month, {}).get("売上実績", 0) or detail.get("sales", 0),
            by_month.get(month, {}).get("売上目標", 0),
            by_month.get("__rate__", {}).get(month, ""),
        ]
        values[idx][4:6] = [detail.get("orders", 0), detail.get("units", by_month.get(month, {}).get("数量", 0))]

    values[2][7:13] = ["ブランド別 SPU 数量 TOP10", "", "", "", "", "Chart: horizontal bar"]
    values[3][7:13] = ["ブランド", "順位", "SPU", "数量", "金額", ""]
    for idx, row in enumerate(_brand_spu_top_units(config), 4):
        values[idx][7:13] = [row.get("ブランド"), row.get("順位"), row.get("SPU"), row.get("数量"), row.get("金額"), ""]

    brand_rows = grouped.get("ブランド別 月次売上", [])
    values[18][:8] = ["ブランド別 月次売上", "", "", "", "", "", "", "Chart: stacked bar/line"]
    values[19][:6] = ["月", *["HRP", "CZUR", "MOFT", "Genki", "HiDock"]]
    brand_month: dict[str, dict[str, float]] = defaultdict(dict)
    for row in brand_rows:
        brand_month[str(row.get("月") or "")][str(row.get("ブランド") or "")] = _number(row.get("金額"))
    for idx, month in enumerate([f"{i}月" for i in range(1, 13)], 20):
        values[idx][:6] = [month, *[brand_month.get(month, {}).get(brand, 0) for brand in ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]]]

    values[59][7:17] = ["年齢段 x ブランド 売上", "", "", "", "", "", "", "", "", "Chart: stacked bar"]
    ages, age_brands, age_pivot = _age_brand_sales(config)
    values[60][7:17] = ["年齢段", *age_brands, "合計"]
    for offset, age in enumerate(ages, 61):
        amounts = [age_pivot.get(age, {}).get(brand, 0) for brand in age_brands]
        values[offset][7:17] = [age, *amounts, sum(amounts)]

    values[42][:5] = ["購入時間帯 売上", "", "", "", "Chart: bar/line"]
    values[43][:5] = ["時", "金額", "数量", "注文数", ""]
    hour_rows = grouped.get("購入時間帯 売上", [])
    for idx, row in enumerate(sorted(hour_rows, key=lambda item: str(item.get("月") or "")), 44):
        values[idx][:5] = [row.get("月"), row.get("金額"), row.get("数量"), _orders(row.get("補足")), ""]
    return values


def _store_sheet_values_v2(store_id: str) -> list[list[Any]]:
    config = _store_config(store_id)
    rows = _build_rows(config)
    grouped = _rows_by_chart(rows)
    detail_month = _detail_month_metrics(config)
    values = _blank(rows=340, cols=28)
    values[0][:4] = [f"{config.label} Dashboard DB", "generated_by", "Codex", "2026-05-28"]

    monthly = grouped.get("月次 売上 vs 目標") or grouped.get("月次 売上") or []
    by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for row in monthly:
        month = str(row.get("月") or "")
        metric = str(row.get("指標") or "")
        by_month[month][metric] = _number(row.get("金額"))

    values[2][:7] = ["月次売上", "", "", "", "", "Chart", "line/bar"]
    values[3][:6] = ["月", "売上実績", "売上目標", "達成率", "件数", "数量"]
    for idx, month in enumerate(MONTHS, 4):
        detail = detail_month.get(month, {})
        actual_sales = detail.get("sales", 0)
        target = by_month.get(month, {}).get("売上目標", 0)
        values[idx][:6] = [
            month,
            actual_sales,
            target,
            actual_sales / target if target else "",
            detail.get("orders", 0),
            detail.get("units", 0),
        ]

    values[2][7:14] = ["当月 ブランド別 SPU 数量 TOP10", "", "", "", "", "", "Chart: horizontal bar"]
    values[3][7:14] = ["月", "ブランド", "順位", "SPU", "数量", "金額", ""]
    for idx, row in enumerate(_brand_spu_monthly_top_units(config), 4):
        values[idx][7:14] = [row.get("月"), row.get("ブランド"), row.get("順位"), row.get("SPU"), row.get("数量"), row.get("金額"), ""]

    values[38][:10] = ["ブランド別 月次売上", "", "", "", "", "", "", "", "", "Chart: stacked bar/line"]
    brand_headers, brand_month = _brand_month_actual_sales(config)
    values[39][:12] = ["月", *brand_headers, "合計"]
    for idx, month in enumerate(MONTHS, 40):
        amounts = [brand_month.get(month, {}).get(brand, 0) for brand in brand_headers]
        values[idx][:12] = [month, *amounts, sum(amounts)]

    values[58][:12] = ["年齢段 x ブランド 売上", "", "", "", "", "", "", "", "", "", "", "Chart: stacked bar"]
    ages, age_brands, age_pivot = _age_brand_sales(config)
    values[59][:12] = ["年齢段", *age_brands, "合計"]
    for offset, age in enumerate(ages, 60):
        amounts = [age_pivot.get(age, {}).get(brand, 0) for brand in age_brands]
        values[offset][:12] = [age, *amounts, sum(amounts)]

    if config.label == "tomtoc":
        values[88][:14] = ["年齢段 x 产品 売上", "", "", "", "", "", "", "", "", "", "", "", "", "Chart: stacked bar"]
        product_ages, products, product_age_pivot = _age_spu_sales(config)
        values[89][:14] = ["年齢段", *products, "合計"]
        for offset, age in enumerate(product_ages, 90):
            amounts = [product_age_pivot.get(age, {}).get(product, 0) for product in products]
            values[offset][:14] = [age, *amounts, sum(amounts)]

        sex_rows = _sex_spu_order_rows(config)
        values[118][:8] = ["性別比率 x 产品", "", "", "", "", "", "", "Chart: stacked bar/ratio"]
        for offset, row in enumerate(sex_rows, 119):
            values[offset][:7] = row

    return values


def _store_sheet_values_ezlife() -> list[list[Any]]:
    config = _store_config("default")
    detail_month = _detail_month_metrics(config)
    dashboard_month = _dashboard_month(detail_month)
    rms_sales = _rms_sales_by_month(config)
    values = _blank(rows=180, cols=14)
    values[0][:8] = ["EZLIFE 仪表盘", "渠道", "楽天", "更新方式", "脚本刷新", "年度", "2026", ""]
    values[2][:6] = ["楽天区域", "", "", "", "", ""]

    targets_by_month = _target_sales_by_month(config)

    values[4][:7] = ["月次売上", "", "", "", "", "", "Chart: line/bar"]
    values[5][:14] = ["指標", *MONTHS, "合計"]
    monthly_metrics = {
        "売上実績": [],
        "売上目標": [],
        "達成率": [],
        "件数": [],
        "数量": [],
    }
    for month in MONTHS:
        detail = detail_month.get(month, {})
        actual_sales = rms_sales.get(month, 0) or detail.get("sales", 0)
        target = targets_by_month.get(month, 0)
        monthly_metrics["売上実績"].append(actual_sales)
        monthly_metrics["売上目標"].append(target)
        monthly_metrics["達成率"].append(actual_sales / target if target else "")
        monthly_metrics["件数"].append(detail.get("orders", 0))
        monthly_metrics["数量"].append(detail.get("units", 0))
    for offset, (metric, row_values) in enumerate(monthly_metrics.items(), 6):
        total = "" if metric == "達成率" else sum(_number(value) for value in row_values)
        values[offset][:14] = [metric, *row_values, total]

    top_rows = _brand_spu_top_blocks_for_month(config, dashboard_month)
    values[20][:7] = [f"{dashboard_month} ブランド別 SPU 金額 TOP10", "", "", "", "", "", "Chart: bar"]
    for idx, row in enumerate(top_rows, 21):
        values[idx][:6] = row

    brand_start = 21 + len(top_rows) + 2
    values[brand_start - 1][:12] = ["ブランド別 月次売上", "", "", "", "", "", "", "", "", "", "", "Chart: stacked bar/line"]
    brand_headers, brand_month = _brand_month_actual_sales(config)
    values[brand_start][:14] = ["ブランド", *MONTHS, "合計"]
    for offset, brand in enumerate(brand_headers, brand_start + 1):
        amounts = [brand_month.get(month, {}).get(brand, 0) for month in MONTHS]
        values[offset][:14] = [brand, *amounts, sum(amounts)]
    monthly_totals = [sum(brand_month.get(month, {}).get(brand, 0) for brand in brand_headers) for month in MONTHS]
    values[brand_start + 1 + len(brand_headers)][:14] = ["合計", *monthly_totals, sum(monthly_totals)]

    age_start = brand_start + 16
    values[age_start - 1][:12] = ["年齢段 x ブランド 売上", "", "", "", "", "", "", "", "", "", "", "Chart: stacked bar"]
    ages, age_brands, age_pivot = _age_brand_sales(config)
    values[age_start][:12] = ["年齢段", *age_brands, "合計"]
    for offset, age in enumerate(ages, age_start + 1):
        amounts = [age_pivot.get(age, {}).get(brand, 0) for brand in age_brands]
        values[offset][:12] = [age, *amounts, sum(amounts)]

    return values


def _ezlife_dashboard_blocks() -> dict[str, tuple[int, int, list[list[Any]]]]:
    config = _store_config("default")
    detail_month = _detail_month_metrics(config)
    dashboard_month = _dashboard_month(detail_month)
    rms_sales = _rms_sales_by_month(config)
    targets_by_month = _target_sales_by_month(config)

    monthly_metrics = {
        "売上実績": [],
        "売上目標": [],
        "達成率": [],
        "件数": [],
        "数量": [],
    }
    for month in MONTHS:
        detail = detail_month.get(month, {})
        actual_sales = rms_sales.get(month, 0) or detail.get("sales", 0)
        target = targets_by_month.get(month, 0)
        monthly_metrics["売上実績"].append(actual_sales)
        monthly_metrics["売上目標"].append(target)
        monthly_metrics["達成率"].append(actual_sales / target if target else "")
        monthly_metrics["件数"].append(detail.get("orders", 0))
        monthly_metrics["数量"].append(detail.get("units", 0))

    monthly_values = []
    for metric, row_values in monthly_metrics.items():
        total = "" if metric == "達成率" else sum(_number(value) for value in row_values)
        monthly_values.append(row_values + [total])

    brand_headers, brand_month = _brand_month_actual_sales(config)
    brand_rows_by_name = {
        brand: [brand_month.get(month, {}).get(brand, 0) for month in MONTHS]
        for brand in brand_headers
    }
    brand_values = []
    for brand in ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]:
        row_values = brand_rows_by_name.get(brand, [0 for _ in MONTHS])
        brand_values.append(row_values + [sum(row_values)])
    monthly_totals = [sum(brand_rows_by_name.get(brand, [0 for _ in MONTHS])[idx] for brand in ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]) for idx in range(len(MONTHS))]
    brand_values.append(monthly_totals + [sum(monthly_totals)])

    top_by_brand: dict[str, list[list[Any]]] = defaultdict(list)
    for row in _brand_spu_top_amount_for_month(config, dashboard_month):
        brand = str(row.get("ブランド") or "")
        top_by_brand[brand].append([row.get("順位"), row.get("SPU"), row.get("数量"), row.get("金額"), row.get("月"), row.get("ブランド")])

    def top_rows(brand: str) -> list[list[Any]]:
        rows = sorted(top_by_brand.get(brand, []), key=lambda item: _number(item[0]))[:10]
        return rows + [["", "", "", "", "", ""] for _ in range(10 - len(rows))]

    ages, age_brands, age_pivot = _age_brand_sales(config)
    age_lookup = {
        age: [age_pivot.get(age, {}).get(brand, 0) for brand in ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]]
        for age in ages
    }
    age_order = ["10代", "10歳未満", "20代", "30代", "40代", "50代", "60代", "70代", "80代", "不明"]
    age_values = []
    for age in age_order:
        row_values = age_lookup.get(age, [0, 0, 0, 0, 0])
        age_values.append(row_values + [sum(row_values)])

    return {
        "top_title": (22, 1, [[f"{dashboard_month} ブランド別 SPU 金額 TOP10"]]),
        "monthly": (7, 2, monthly_values),  # B7:N11
        "brand_monthly": (15, 2, brand_values),  # B15:N20
        "top_hrp": (25, 1, top_rows("HRP")),  # A25:F34
        "top_czur": (25, 8, top_rows("CZUR")),  # H25:M34
        "top_moft": (39, 1, top_rows("MOFT")),  # A39:F48
        "top_hidock": (39, 8, top_rows("HiDock")[:3]),  # H39:M41
        "top_genki": (45, 8, top_rows("Genki")[:4]),  # H45:M48
        "age_brand": (53, 2, age_values),  # B53:G62
    }


def _base_link_values(store_ids: tuple[str, ...] = ("default", "store2")) -> list[list[Any]]:
    header = ["店舗", "キー", "グラフ", "月", "指標", "ブランド", "SPU", "値", "金額", "数量", "達成率", "順位", "補足"]
    values: list[list[Any]] = [header]
    for store_id in store_ids:
        config = _store_config(store_id)
        for row in _build_rows(config):
            values.append([row.get(col, "") for col in header])
    return values


def run_dashboard_sheet_export(store_id: str | None = None, include_base_link: bool = True) -> dict[str, Any]:
    _RECORD_CACHE.clear()
    selected_store_id = (store_id or "").strip()
    store_ids = (selected_store_id or "default",)
    for selected in store_ids:
        if selected not in STORE_SHEETS:
            raise ValueError(f"Unsupported dashboard sheet store_id: {selected}")

    sheets: dict[str, str] = {}
    written_sheets: dict[str, str] = {}
    rows_by_block: dict[str, int] = {}
    for selected in store_ids:
        title = STORE_SHEETS[selected]
        sheets[title] = _ensure_sheet(title, 0)
        if selected == "default":
            rows_by_block = _write_ezlife_dashboard_data(sheets[title])
        else:
            values = _store_sheet_values_v2(selected)
            _write(sheets[title], 1, 1, values)
        written_sheets[title] = sheets[title]

    base_link_rows = 0
    if include_base_link:
        sheets[BASE_LINK_SHEET] = _ensure_sheet(BASE_LINK_SHEET, 1)
        base_values = _base_link_values(store_ids)
        _write(sheets[BASE_LINK_SHEET], 1, 1, _blank(rows=max(220, len(base_values) + 20), cols=14))
        _write(sheets[BASE_LINK_SHEET], 1, 1, base_values)
        written_sheets[BASE_LINK_SHEET] = sheets[BASE_LINK_SHEET]
        base_link_rows = len(base_values) - 1

    return {
        "success": True,
        "spreadsheet_token": SPREADSHEET_TOKEN,
        "store_ids": store_ids,
        "sheets": written_sheets,
        "base_link_rows": base_link_rows,
        "rows_by_block": rows_by_block,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_dashboard_sheet_export(), ensure_ascii=False, indent=2))
