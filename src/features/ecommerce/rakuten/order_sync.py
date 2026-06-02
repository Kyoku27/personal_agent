from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

JST = timezone(timedelta(hours=9))

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "order_number": ("\u6ce8\u6587\u756a\u53f7", "\u53d7\u6ce8\u756a\u53f7", "orderNumber"),
    "order_date": ("\u6ce8\u6587\u65e5", "\u6ce8\u6587\u65e5\u6642", "\u53d7\u6ce8\u65e5"),
    "item_name": ("\u5546\u54c1\u540d",),
    "sku": ("\u5546\u54c1\u7ba1\u7406\u756a\u53f7", "SKU\u7ba1\u7406\u756a\u53f7", "SKU", "\u5546\u54c1\u756a\u53f7", "manageNumber"),
    "system_sku": ("\u30b7\u30b9\u30c6\u30e0\u9023\u643a\u7528SKU\u756a\u53f7", "\u30b7\u30b9\u30c6\u30e0SKU", "systemSku"),
    "qty": ("\u6570\u91cf", "\u500b\u6570", "units"),
    "unit_price": ("\u5358\u4fa1", "\u8ca9\u58f2\u5358\u4fa1", "price"),
    "subtotal": ("\u5c0f\u8a08", "\u5546\u54c1\u5408\u8a08", "subtotal"),
    "shipping_fee": ("\u9001\u6599",),
    "total": ("\u8acb\u6c42\u91d1\u984d", "\u5408\u8a08\u91d1\u984d", "\u58f2\u4e0a\u91d1\u984d", "total"),
    "customer": ("\u6ce8\u6587\u8005", "\u8cfc\u5165\u8005", "\u9867\u5ba2\u540d"),
    "prefecture": ("\u90fd\u9053\u5e9c\u770c", "\u6ce8\u6587\u8005\u90fd\u9053\u5e9c\u770c"),
    "settlement": ("\u6c7a\u6e08\u65b9\u6cd5", "\u652f\u6255\u65b9\u6cd5"),
    "status": ("\u6ce8\u6587\u30b9\u30c6\u30fc\u30bf\u30b9", "\u30b9\u30c6\u30fc\u30bf\u30b9"),
    "reference_code": ("\u53c2\u7167\u30b3\u30fc\u30c9", "\u53c2\u8003code", "orderProgressCode"),
    "month_tag": ("\u6708", "\u6708\u5225", "\u5bfe\u8c61\u6708Tag"),
    "sex": ("\u6027\u5225", "sex", "gender"),
    "birth_year": ("\u51fa\u751f\u5e74", "\u751f\u5e74", "birthYear"),
    "age_bucket": ("\u5e74\u9f62\u6bb5", "\u5e74\u4ee3", "ageBucket"),
    "purchase_hour": ("\u8cfc\u5165\u6642", "\u8cfc\u5165\u6642\u9593", "\u6ce8\u6587\u6642", "purchaseHour"),
    "purchase_time_range": ("\u8cfc\u5165\u6642\u9593\u5e2f", "\u6642\u9593\u5e2f", "purchaseTimeRange"),
}

PROFILE_FIELD_DEFINITIONS: list[dict[str, Any]] = [
    {"field_name": "\u6027\u5225", "type": 1},
    {"field_name": "\u51fa\u751f\u5e74", "type": 1},
    {"field_name": "\u5e74\u9f62\u6bb5", "type": 1},
    {"field_name": "\u8cfc\u5165\u6642", "type": 1},
    {"field_name": "\u8cfc\u5165\u6642\u9593\u5e2f", "type": 1},
]


def _find_field(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {c.strip(): c for c in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    for alias in aliases:
        for col in columns:
            if alias and alias in col:
                return col
    return None


def build_column_mapping(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, aliases in FIELD_ALIASES.items():
        found = _find_field(columns, aliases)
        if found:
            mapping[key] = found
    return mapping


def decide_granularity(columns: list[str]) -> str:
    mapping = build_column_mapping(columns)
    return "per_item" if mapping.get("item_name") or mapping.get("sku") else "per_order"


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, JST)
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=JST)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=JST)
    except ValueError:
        return None


def as_lark_date_ms(value: Any) -> int | None:
    dt = _parse_datetime(value)
    if not dt:
        return None
    midnight = datetime.combine(dt.astimezone(JST).date(), time.min, JST)
    return int(midnight.timestamp() * 1000)


def _set(fields: dict[str, Any], mapping: dict[str, str], key: str, value: Any) -> None:
    field_name = mapping.get(key)
    if field_name and value not in (None, ""):
        fields[field_name] = value


def _order_date(value: Any) -> date | None:
    dt = _parse_datetime(value)
    return dt.astimezone(JST).date() if dt else None


def _purchase_hour(value: Any) -> str:
    dt = _parse_datetime(value)
    return f"{dt.astimezone(JST).hour:02d}" if dt else ""


def _purchase_time_range(value: Any) -> str:
    hour = _purchase_hour(value)
    return f"{hour}:00-{hour}:59" if hour else ""


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _age_bucket(order_datetime: Any, birth_year: Any, birth_month: Any = None, birth_day: Any = None) -> str:
    year = _as_int(birth_year)
    if not year:
        return ""
    order_day = _order_date(order_datetime) or date.today()
    age = order_day.year - year
    month = _as_int(birth_month)
    day = _as_int(birth_day)
    if month and day and (order_day.month, order_day.day) < (month, day):
        age -= 1
    if age < 0:
        return ""
    if age < 10:
        return "10\u6b73\u672a\u6e80"
    return f"{(age // 10) * 10}\u4ee3"


def build_records(orders: list[dict[str, Any]], columns: list[str], granularity: str | None = None) -> list[tuple[dict[str, Any], dict[str, str]]]:
    mapping = build_column_mapping(columns)
    granularity = granularity or decide_granularity(columns)
    records: list[tuple[dict[str, Any], dict[str, str]]] = []

    for order in orders:
        order_no = str(order.get("orderNumber") or "").strip()
        order_date_value = order.get("orderDatetime")
        order_day = _order_date(order_date_value)
        month_tag = f"{order_day.month}\u6708" if order_day else ""
        base_fields: dict[str, Any] = {}
        _set(base_fields, mapping, "order_number", order_no)
        _set(base_fields, mapping, "order_date", as_lark_date_ms(order_date_value))
        _set(base_fields, mapping, "shipping_fee", order.get("shippingFee"))
        _set(base_fields, mapping, "total", order.get("requestPrice") or order.get("totalPrice"))
        _set(base_fields, mapping, "customer", order.get("ordererName"))
        _set(base_fields, mapping, "prefecture", order.get("ordererPrefecture"))
        _set(base_fields, mapping, "settlement", order.get("settlementMethod"))
        _set(base_fields, mapping, "status", order.get("orderStatus"))
        _set(base_fields, mapping, "reference_code", order.get("orderProgressCode"))
        _set(base_fields, mapping, "month_tag", month_tag)
        _set(base_fields, mapping, "sex", order.get("ordererSex"))
        birth_year = order.get("ordererBirthYear")
        _set(base_fields, mapping, "birth_year", str(birth_year) if birth_year not in (None, "") else "")
        _set(
            base_fields,
            mapping,
            "age_bucket",
            _age_bucket(
                order_date_value,
                order.get("ordererBirthYear"),
                order.get("ordererBirthMonth"),
                order.get("ordererBirthDay"),
            ),
        )
        _set(base_fields, mapping, "purchase_hour", _purchase_hour(order_date_value))
        _set(base_fields, mapping, "purchase_time_range", _purchase_time_range(order_date_value))

        if granularity == "per_order":
            records.append((base_fields, {"order_number": order_no, "sku": ""}))
            continue

        for item in order.get("items") or [{}]:
            fields = dict(base_fields)
            sku = str(item.get("variantId") or item.get("systemSku") or item.get("manageNumber") or item.get("itemNumber") or "").strip()
            item_name = str(item.get("itemName") or "").strip()
            qty = item.get("units") or 1
            price = item.get("price") or 0
            try:
                subtotal = float(price) * int(qty)
            except (TypeError, ValueError):
                subtotal = price
            _set(fields, mapping, "item_name", item_name)
            _set(fields, mapping, "sku", sku)
            _set(fields, mapping, "system_sku", item.get("systemSku"))
            _set(fields, mapping, "qty", qty)
            _set(fields, mapping, "unit_price", price)
            _set(fields, mapping, "subtotal", subtotal)
            system_sku = str(item.get("systemSku") or "").strip()
            records.append((fields, {"order_number": order_no, "sku": system_sku or sku or item_name}))
    return records
