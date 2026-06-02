from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Any

os.environ["LARK_USER_ACCESS_TOKEN"] = ""

from run_yearly_dashboard_charts import _records, _store_config, _text
from src.features.ecommerce.rakuten.api_client import RakutenApiClient

META_HEADERS = [
    "email",
    "phone",
    "zip",
    "country",
    "dob",
    "gen",
]

EMAIL_KEYS = ("email", "mail", "mailaddress", "emailaddress", "ordereremail", "orderermail")
PHONE_KEYS = ("phone", "tel", "telephone", "phonenumber", "telnumber", "ordererphone", "orderertel")
ZIP_KEYS = ("zipcode", "postalcode", "postno", "zip")
PREFECTURE_KEYS = ("prefecture", "state", "province")
CITY_KEYS = ("city",)
ADDRESS_KEYS = ("ordereraddress", "senderaddress", "address1", "address2", "address3", "addr1", "addr2", "addr3")
NAME_KEYS = ("orderername",)
FIRST_NAME_KEYS = ("firstname", "name1")
LAST_NAME_KEYS = ("lastname", "name2")
SEX_KEYS = ("sex", "gender")
BIRTH_YEAR_KEYS = ("birthyear",)
BIRTH_MONTH_KEYS = ("birthmonth",)
BIRTH_DAY_KEYS = ("birthday", "birthdate")


@dataclass
class MetaAudienceExport:
    filename: str
    csv_text: str
    store_id: str
    store_label: str
    start_date: str
    end_date: str
    spu: str
    orders_count: int
    audience_rows: int


def _canonical_store_id(store_id: str | None) -> str:
    key = (store_id or "default").strip().lower()
    if key in {"ezlife", "default"}:
        return "default"
    if key in {"tomtoc", "store2"}:
        return "store2"
    return key


def _store_label(store_id: str) -> str:
    return "tomtoc" if _canonical_store_id(store_id) == "store2" else "EZLIFE"


def _parse_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _resolve_range(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    start = _parse_date(start_date or "2026-03-01", "start_date")
    end = _parse_date(end_date or date.today().isoformat(), "end_date")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    return start.isoformat(), end.isoformat()


def _date_chunks(start_date: str, end_date: str, max_days: int = 31) -> list[tuple[str, str]]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _normalized_keys(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, list) else [value]
    keys: list[str] = []
    for item in values:
        text = _text(item).strip()
        if not text:
            continue
        keys.extend([text, text.upper(), text.lower(), text.replace("_", "-"), text.replace("-", "_")])
    return list(dict.fromkeys(keys))


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            pairs.extend(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            pairs.extend(_flatten(child, f"{prefix}[{index}]"))
    else:
        pairs.append((prefix, value))
    return pairs


def _matching_values(raw: dict[str, Any], hints: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for path, value in _flatten(raw):
        key = _norm_key(path)
        if any(hint in key for hint in hints):
            text = str(value or "").strip()
            if text:
                values.append(text)
    return values


def _first_value(raw: dict[str, Any], hints: tuple[str, ...]) -> str:
    values = _matching_values(raw, hints)
    return values[0] if values else ""


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _normalize_phone(value: str, country_code: str = "81") -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(country_code):
        return digits
    if digits.startswith("0"):
        return f"{country_code}{digits[1:]}"
    return digits


def _orderer_model(raw: dict[str, Any]) -> dict[str, Any]:
    orderer = raw.get("OrdererModel")
    return orderer if isinstance(orderer, dict) else {}


def _orderer_phone(raw: dict[str, Any]) -> str:
    orderer = _orderer_model(raw)
    parts = [
        str(orderer.get("phoneNumber1") or "").strip(),
        str(orderer.get("phoneNumber2") or "").strip(),
        str(orderer.get("phoneNumber3") or "").strip(),
    ]
    joined = "".join(parts)
    normalized = _normalize_phone(joined or _first_value(raw, PHONE_KEYS))
    return f"+{normalized}" if normalized else ""


def _orderer_zip(raw: dict[str, Any]) -> str:
    orderer = _orderer_model(raw)
    parts = [
        str(orderer.get("zipCode1") or "").strip(),
        str(orderer.get("zipCode2") or "").strip(),
    ]
    joined = "".join(parts)
    return _normalize_zip(joined or _first_value(raw, ZIP_KEYS))


def _orderer_gender(raw: dict[str, Any]) -> str:
    value = str(_orderer_model(raw).get("sex") or _first_value(raw, SEX_KEYS)).strip().lower()
    if value in {"女", "女性", "f", "female", "2"}:
        return "f"
    if value in {"男", "男性", "m", "male", "1"}:
        return "m"
    return ""


def _normalize_zip(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).lower()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if len(parts) >= 2:
        return " ".join(parts[1:]), parts[0]
    return "", name.strip()


def _birth_date(raw: dict[str, Any]) -> str:
    orderer = _orderer_model(raw)
    year = re.sub(r"\D", "", str(orderer.get("birthYear") or _first_value(raw, BIRTH_YEAR_KEYS)))
    month = re.sub(r"\D", "", str(orderer.get("birthMonth") or _first_value(raw, BIRTH_MONTH_KEYS)))
    day = re.sub(r"\D", "", str(orderer.get("birthDay") or _first_value(raw, BIRTH_DAY_KEYS)))
    if len(year) == 4 and month and day:
        return f"{year}{int(month):02d}{int(day):02d}"
    return ""


def _fetch_raw_orders(store_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    client = RakutenApiClient(store_id=store_id)
    order_numbers: list[str] = []
    seen: set[str] = set()
    for range_start, range_end in _date_chunks(start_date, end_date):
        page = 1
        while True:
            result = client.search_orders(range_start, range_end, page)
            for order_number in result.get("orderNumberList") or []:
                if order_number not in seen:
                    order_numbers.append(order_number)
                    seen.add(order_number)
            pagination = result.get("PaginationResponseModel") or {}
            total_pages = int(pagination.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1

    orders: list[dict[str, Any]] = []
    for index in range(0, len(order_numbers), 100):
        detail = client.get_order_items(order_numbers[index:index + 100])
        orders.extend(detail.get("OrderModelList") or [])
    return orders


def _spu_catalog(store_id: str) -> tuple[set[str], dict[str, str]]:
    config = _store_config(store_id)
    valid_spus: set[str] = set()
    sku_to_spu: dict[str, str] = {}
    for item in _records(config, config.spu_monthly_table):
        fields = item.get("fields") or {}
        spu = _text(fields.get("SPU")).strip()
        if not spu:
            continue
        valid_spus.add(spu)
        for key_field, value in fields.items():
            key_norm = _norm_key(key_field)
            if key_norm in {"spu", "tagspu"} or "sku" in key_norm or "manage" in key_norm or "item" in key_norm:
                for key in _normalized_keys(value):
                    sku_to_spu[key] = spu
        for key in _normalized_keys(spu):
            sku_to_spu[key] = spu
    return valid_spus, sku_to_spu


def _resolve_spu(spu: str, valid_spus: set[str]) -> str:
    target = spu.strip()
    if not target:
        return ""
    matches = [candidate for candidate in valid_spus if candidate.lower() == target.lower()]
    if not matches:
        raise ValueError(f"没有找到 SPU：{target}")
    return matches[0]


def _item_skus(raw: dict[str, Any]) -> list[str]:
    skus: list[str] = []
    for package in raw.get("PackageModelList") or []:
        for item in package.get("ItemModelList") or []:
            for key in ("manageNumber", "itemNumber"):
                value = _text(item.get(key)).strip()
                if value:
                    skus.append(value)
            for sku_model in item.get("SkuModelList") or []:
                for key in ("merchantDefinedSkuId", "variantId"):
                    value = _text(sku_model.get(key)).strip()
                    if value:
                        skus.append(value)
    return list(dict.fromkeys(skus))


def _matched_spus(raw: dict[str, Any], sku_to_spu: dict[str, str]) -> tuple[list[str], list[str]]:
    matched_spus: list[str] = []
    matched_skus: list[str] = []
    for sku in _item_skus(raw):
        for key in _normalized_keys(sku):
            prefix = key.split("-", 1)[0].split("_", 1)[0]
            spu = sku_to_spu.get(key) or sku_to_spu.get(prefix)
            if spu:
                matched_spus.append(spu)
                matched_skus.append(sku)
                break
    return list(dict.fromkeys(matched_spus)), list(dict.fromkeys(matched_skus))


def _audience_row(raw: dict[str, Any], matched_spus: list[str], matched_skus: list[str]) -> list[Any]:
    return [
        _normalize_email(_first_value(raw, EMAIL_KEYS)),
        _orderer_phone(raw),
        _orderer_zip(raw),
        "jp",
        _birth_date(raw),
        _orderer_gender(raw),
    ]


def _csv_text(rows: list[list[Any]]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return "\ufeff" + buffer.getvalue()


def build_meta_audience_export(
    store_id: str = "default",
    start_date: str | None = None,
    end_date: str | None = None,
    spu: str | None = None,
) -> MetaAudienceExport:
    store_id = _canonical_store_id(store_id)
    start, end = _resolve_range(start_date, end_date)
    valid_spus, sku_to_spu = _spu_catalog(store_id)
    target_spu = _resolve_spu(spu or "", valid_spus)

    raw_orders = _fetch_raw_orders(store_id, start, end)
    rows: list[list[Any]] = [META_HEADERS]
    for raw in raw_orders:
        matched_spus, matched_skus = _matched_spus(raw, sku_to_spu)
        if target_spu and target_spu not in matched_spus:
            continue
        row = _audience_row(raw, matched_spus, matched_skus)
        if not any(row):
            continue
        rows.append(row)

    label = _store_label(store_id)
    filename_spu = re.sub(r"[^0-9A-Za-z._-]+", "_", target_spu or "ALL")
    filename = f"meta_audience_{label}_{start}_to_{end}_{filename_spu}.csv"
    return MetaAudienceExport(
        filename=filename,
        csv_text=_csv_text(rows),
        store_id=store_id,
        store_label=label,
        start_date=start,
        end_date=end,
        spu=target_spu,
        orders_count=len(raw_orders),
        audience_rows=max(len(rows) - 1, 0),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", default="default")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--spu")
    args = parser.parse_args()
    export = build_meta_audience_export(args.store_id, args.start_date, args.end_date, args.spu)
    print(export.csv_text)
