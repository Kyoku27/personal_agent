from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.daily_template import (
    _batch_update_records,
    _create_field,
    _list_fields,
    _list_records,
    _list_tables,
)

JST = timezone(timedelta(hours=9))

MONTHLY_TARGET_TABLE_NAME = "\u58f2\u4e0a\u76ee\u6a19_\u5e74\u9593"
YEAR_TARGET_TABLE_NAME = "\u58f2\u4e0a\u76ee\u6a19_\u5e74\u5ea6"

YEAR_FIELD = "\u5e74\u5225"
MONTH_FIELD = "\u6708\u5225"
ORDER_DATE_FIELD = "\u6ce8\u6587\u65e5"
REFERENCE_CODE_FIELD = "\u53c2\u8003code"
AMOUNT_AFTER_DISCOUNT_FIELD = "\u5408\u8a08\u91d1\u984d\uff08\u6298\u540e\uff09"
BRAND_TAG_FIELD = "Tag2HRP"

BRAND_TAGS = {
    "HRP": {"optJcVYchp", "HRP"},
    "CZUR": {"optCJT0Rug", "CZUR"},
    "MOFT": {"opthFCt79h", "MOFT"},
    "Genki": {"optgJM6Kh4", "Genki"},
    "HiDock": {"optvctlEqS", "Hidock", "HiDock"},
}

KNOWN_RMS_MONTHLY_SALES = {
    2026: {
        1: 9_274_685,
        2: 9_912_007,
        3: 13_486_216,
        4: 10_686_061,
        5: 11_856_520,
    }
}


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("\u00a5", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _month_number(value: Any) -> int | None:
    match = re.search(r"(\d{1,2})", str(value or ""))
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None


def _record_year(fields: dict[str, Any]) -> int | None:
    value = fields.get(ORDER_DATE_FIELD)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, JST).year
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _brand_from_value(value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    normalized = {str(item.get("text") or item.get("name") or item).strip() if isinstance(item, dict) else str(item).strip() for item in values}
    for brand, aliases in BRAND_TAGS.items():
        if normalized & aliases:
            return brand
    return None


def _parse_rms_sales_env(target_year: int) -> dict[int, int]:
    configured = dict(KNOWN_RMS_MONTHLY_SALES.get(target_year, {}))
    raw = get_env(f"RAKUTEN_RMS_MONTHLY_SALES_{target_year}", "") or ""
    if not raw:
        return configured
    for part in raw.split(","):
        if not part.strip() or "=" not in part:
            continue
        key, value = part.split("=", 1)
        month = _month_number(key)
        if month:
            configured[month] = int(_number(value))
    return configured


def _find_table_id(tables: list[dict[str, Any]], name: str) -> str:
    table = next((item for item in tables if item.get("name") == name), None)
    if not table:
        raise RuntimeError(f"Table {name} was not found.")
    return str(table["table_id"])


def _actual_field_names(suffix: str) -> list[str]:
    return [f"{brand}_\u5b9f\u7e3e_{suffix}" for brand in BRAND_TAGS] + [f"\u58f2\u4e0a_\u5b9f\u7e3e_{suffix}"]


def _display_rate_field_names() -> list[str]:
    return [
        "\u9054\u6210\u7387_\u8868\u793a",
        "\u9054\u6210\u7387_HRP_\u8868\u793a",
        "\u9054\u6210\u7387_CZUR_\u8868\u793a",
        "\u9054\u6210\u7387_MOFT_\u8868\u793a",
    ]


def _ensure_actual_fields(app_token: str, table_id: str) -> list[str]:
    existing = {field.get("field_name") for field in _list_fields(app_token, table_id, prefer_user=True)}
    created: list[str] = []
    currency_fields = _actual_field_names("\u6298\u540e") + _actual_field_names("\u8868\u793a") + ["RMS\u58f2\u4e0a"]
    for field_name in currency_fields:
        if field_name in existing:
            continue
        _create_field(
            app_token,
            table_id,
            {
                "field_name": field_name,
                "type": 2,
                "property": {"currency_code": "JPY", "formatter": "0"},
            },
            prefer_user=True,
        )
        created.append(field_name)
        existing.add(field_name)
    for field_name in _display_rate_field_names():
        if field_name in existing:
            continue
        _create_field(
            app_token,
            table_id,
            {
                "field_name": field_name,
                "type": 2,
                "property": {"formatter": "0.00%"},
            },
            prefer_user=True,
        )
        created.append(field_name)
        existing.add(field_name)
    return created


def _summarize_order_details(app_token: str, order_table_id: str, target_year: int) -> dict[int, dict[str, float]]:
    summary: dict[int, dict[str, float]] = {month: defaultdict(float) for month in range(1, 13)}
    for record in _list_records(app_token, order_table_id, prefer_user=True):
        fields = record.get("fields") or {}
        record_year = _record_year(fields)
        if record_year is not None and record_year != target_year:
            continue
        month = _month_number(fields.get(MONTH_FIELD))
        if not month:
            continue
        if str(fields.get(REFERENCE_CODE_FIELD)) == "900":
            continue
        brand = _brand_from_value(fields.get(BRAND_TAG_FIELD))
        if not brand:
            continue
        amount = _number(fields.get(AMOUNT_AFTER_DISCOUNT_FIELD))
        summary[month][brand] += amount
        summary[month]["total"] += amount
    return summary


def _rms_adjusted_values(summary: dict[str, float], rms_total: int | None) -> dict[str, int]:
    total = float(summary.get("total") or 0)
    target_total = int(rms_total if rms_total is not None else round(total))
    values: dict[str, int] = {"total": target_total}
    if target_total <= 0 or total <= 0:
        values.update({brand: 0 for brand in BRAND_TAGS})
        return values

    running = 0
    brands = list(BRAND_TAGS)
    for brand in brands[:-1]:
        value = round(target_total * float(summary.get(brand) or 0) / total)
        values[brand] = value
        running += value
    values[brands[-1]] = target_total - running
    return values


def update_sales_target_actuals(app_token: str, order_table_id: str, target_year: int = 2026) -> dict[str, Any]:
    tables = _list_tables(app_token, prefer_user=True)
    monthly_table_id = _find_table_id(tables, MONTHLY_TARGET_TABLE_NAME)
    year_table_id = _find_table_id(tables, YEAR_TARGET_TABLE_NAME)
    created_monthly = _ensure_actual_fields(app_token, monthly_table_id)
    created_year = _ensure_actual_fields(app_token, year_table_id)

    summary = _summarize_order_details(app_token, order_table_id, target_year)
    rms_totals = _parse_rms_sales_env(target_year)
    adjusted = {month: _rms_adjusted_values(summary[month], rms_totals.get(month)) for month in range(1, 13)}

    monthly_updates: list[dict[str, Any]] = []
    for record in _list_records(app_token, monthly_table_id, prefer_user=True):
        fields = record.get("fields") or {}
        if str(fields.get(YEAR_FIELD) or "") != str(target_year):
            continue
        month = _month_number(fields.get(MONTH_FIELD))
        if not month:
            continue
        update_fields: dict[str, Any] = {}
        for brand in BRAND_TAGS:
            update_fields[f"{brand}_\u5b9f\u7e3e_\u6298\u540e"] = round(summary[month].get(brand) or 0)
            update_fields[f"{brand}_\u5b9f\u7e3e_\u8868\u793a"] = adjusted[month][brand]
        update_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u6298\u540e"] = round(summary[month].get("total") or 0)
        update_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u8868\u793a"] = adjusted[month]["total"]
        update_fields["RMS\u58f2\u4e0a"] = adjusted[month]["total"]
        total_target = _number(fields.get("\u58f2\u4e0a\u76ee\u6a19"))
        hrp_target = _number(fields.get("HRP_\u76ee\u6a19"))
        czur_target = _number(fields.get("CZUR_\u76ee\u6a19"))
        moft_target = _number(fields.get("MOFT_\u76ee\u6a19"))
        update_fields["\u9054\u6210\u7387_\u8868\u793a"] = adjusted[month]["total"] / total_target if total_target else 0
        update_fields["\u9054\u6210\u7387_HRP_\u8868\u793a"] = adjusted[month]["HRP"] / hrp_target if hrp_target else 0
        update_fields["\u9054\u6210\u7387_CZUR_\u8868\u793a"] = adjusted[month]["CZUR"] / czur_target if czur_target else 0
        update_fields["\u9054\u6210\u7387_MOFT_\u8868\u793a"] = adjusted[month]["MOFT"] / moft_target if moft_target else 0
        monthly_updates.append({"record_id": record["record_id"], "fields": update_fields})
    _batch_update_records(app_token, monthly_table_id, monthly_updates, prefer_user=True)

    year_fields: dict[str, Any] = {}
    for brand in BRAND_TAGS:
        year_fields[f"{brand}_\u5b9f\u7e3e_\u6298\u540e"] = round(sum(summary[month].get(brand) or 0 for month in range(1, 13)))
        year_fields[f"{brand}_\u5b9f\u7e3e_\u8868\u793a"] = sum(adjusted[month][brand] for month in range(1, 13))
    year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u6298\u540e"] = round(sum(summary[month].get("total") or 0 for month in range(1, 13)))
    year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u8868\u793a"] = sum(adjusted[month]["total"] for month in range(1, 13))
    year_fields["RMS\u58f2\u4e0a"] = year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u8868\u793a"]

    year_updates: list[dict[str, Any]] = []
    for record in _list_records(app_token, year_table_id, prefer_user=True):
        fields = record.get("fields") or {}
        if str(fields.get(YEAR_FIELD) or "") == str(target_year):
            total_target = _number(fields.get("\u58f2\u4e0a\u76ee\u6a19"))
            hrp_target = _number(fields.get("HRP_\u76ee\u6a19"))
            czur_target = _number(fields.get("CZUR_\u76ee\u6a19"))
            moft_target = _number(fields.get("MOFT_\u76ee\u6a19"))
            year_fields["\u9054\u6210\u7387_\u8868\u793a"] = year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u8868\u793a"] / total_target if total_target else 0
            year_fields["\u9054\u6210\u7387_HRP_\u8868\u793a"] = year_fields["HRP_\u5b9f\u7e3e_\u8868\u793a"] / hrp_target if hrp_target else 0
            year_fields["\u9054\u6210\u7387_CZUR_\u8868\u793a"] = year_fields["CZUR_\u5b9f\u7e3e_\u8868\u793a"] / czur_target if czur_target else 0
            year_fields["\u9054\u6210\u7387_MOFT_\u8868\u793a"] = year_fields["MOFT_\u5b9f\u7e3e_\u8868\u793a"] / moft_target if moft_target else 0
            year_updates.append({"record_id": record["record_id"], "fields": year_fields})
    _batch_update_records(app_token, year_table_id, year_updates, prefer_user=True)

    return {
        "target_year": target_year,
        "monthly_updates": len(monthly_updates),
        "year_updates": len(year_updates),
        "created_monthly_fields": created_monthly,
        "created_year_fields": created_year,
        "rms_months": sorted(rms_totals),
        "display_total": year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u8868\u793a"],
        "after_discount_total": year_fields["\u58f2\u4e0a_\u5b9f\u7e3e_\u6298\u540e"],
    }
