from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timezone, timedelta
from typing import Any


Granularity = str  # "per_order" | "per_item"
JST = timezone(timedelta(hours=9))


def _as_date_str(v: Any) -> str:
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime):
        return v.date().strftime("%Y-%m-%d")
    s = str(v or "").strip()
    if not s:
        return ""
    # Rakuten may return like: 2026-04-30T10:23:11+09:00 or 2026-04-30T10:23:11+0900
    return s[:10]


def _as_lark_datetime_ms(v: Any) -> int | None:
    """
    Convert date/datetime-like value into Lark bitable Datetime milliseconds.

    Rakuten returns orderDatetime with a time component, but our Lark daily
    templates compare 注文日 against formula dates such as 1日付/2日付. Store
    order dates at JST midnight so those lookups match the whole day.
    """
    if v is None:
        return None

    def at_jst_midnight(d: date) -> int:
        dtv = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=JST)
        return int(dtv.timestamp() * 1000)

    if isinstance(v, datetime):
        dtv = v if v.tzinfo else v.replace(tzinfo=JST)
        return at_jst_midnight(dtv.astimezone(JST).date())
    if isinstance(v, date):
        return at_jst_midnight(v)

    s = str(v).strip()
    if not s:
        return None

    # 2026-04-30T10:23:11+09:00 -> fromisoformat can parse
    try:
        dtv = datetime.fromisoformat(s)
        if dtv.tzinfo is None:
            dtv = dtv.replace(tzinfo=JST)
        return at_jst_midnight(dtv.astimezone(JST).date())
    except Exception:
        pass

    # fallback: YYYY-MM-DD
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
        return at_jst_midnight(d)
    except Exception:
        return None


def _num(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _int_or_none(v: Any) -> int | None:
    try:
        s = str(v).strip()
        if not s:
            return None
        return int(s)
    except Exception:
        return None


FIELD_ALIASES: dict[str, list[str]] = {
    "order_number": ["受注番号", "注文番号", "オーダー番号", "订单号", "注文番号(受注番号)"],
    "order_date": ["注文日", "受注日", "売上日", "日付", "日期", "注文日時", "受注日時"],
    "item_name": ["商品名", "商品", "品名"],
    "sku": ["SKU", "商品管理番号", "管理番号", "manageNumber", "商品番号", "SKU管理番号"],
    "system_sku": ["システム連携用SKU番号", "システム連携SKU番号", "システム連携SKU", "システムSKU"],
    "qty": ["数量", "個数", "件数", "数", "数量(個数)"],
    "unit_price": ["単価", "単価(税込)", "価格", "単価（税別）", "単価（税込）"],
    "subtotal": ["小計", "小計金額", "商品小計"],
    "shipping_fee": ["送料", "配送料", "送料金額"],
    "total": ["合計", "合計金額", "売上", "営業額", "請求金額", "受注金額"],
    "customer": ["顧客名", "注文者", "氏名", "注文者名"],
    "prefecture": ["都道府県", "県", "エリア", "都道府県名"],
    "settlement": ["決済方法", "支払方法", "決済", "支払"],
    "status": ["ステータス", "状態", "進捗", "注文ステータス"],
    "reference_code": ["参考code", "参考コード", "ref_code", "reference_code", "code"],
    "month_tag": ["月別", "月别", "月タグ", "月tag", "{月别}", "{月別}"],
}


def _match_column(columns: list[str], aliases: list[str]) -> str | None:
    # exact match first, then substring match
    col_set = set(columns)
    for a in aliases:
        if a in col_set:
            return a
    low_cols = {c.lower(): c for c in columns}
    for a in aliases:
        al = a.lower()
        if al in low_cols:
            return low_cols[al]
    for c in columns:
        cl = c.lower()
        for a in aliases:
            if a.lower() in cl:
                return c
    return None


def decide_granularity(columns: list[str]) -> Granularity:
    """
    启发式：如果表里有 SKU/商品名/数量等字段，默认按商品明细写（per_item），否则按订单写（per_order）。
    """
    sku_col = _match_column(columns, FIELD_ALIASES["sku"])
    item_col = _match_column(columns, FIELD_ALIASES["item_name"])
    qty_col = _match_column(columns, FIELD_ALIASES["qty"])
    system_sku_col = _match_column(columns, FIELD_ALIASES["system_sku"])
    if sku_col or system_sku_col or item_col or qty_col:
        return "per_item"
    return "per_order"


@dataclass(frozen=True)
class ColumnMapping:
    order_number: str | None
    order_date: str | None
    item_name: str | None
    sku: str | None
    system_sku: str | None
    qty: str | None
    unit_price: str | None
    subtotal: str | None
    shipping_fee: str | None
    total: str | None
    customer: str | None
    prefecture: str | None
    settlement: str | None
    status: str | None
    reference_code: str | None
    month_tag: str | None


def build_column_mapping(columns: list[str]) -> ColumnMapping:
    return ColumnMapping(
        order_number=_match_column(columns, FIELD_ALIASES["order_number"]),
        order_date=_match_column(columns, FIELD_ALIASES["order_date"]),
        item_name=_match_column(columns, FIELD_ALIASES["item_name"]),
        sku=_match_column(columns, FIELD_ALIASES["sku"]),
        system_sku=_match_column(columns, FIELD_ALIASES["system_sku"]),
        qty=_match_column(columns, FIELD_ALIASES["qty"]),
        unit_price=_match_column(columns, FIELD_ALIASES["unit_price"]),
        subtotal=_match_column(columns, FIELD_ALIASES["subtotal"]),
        shipping_fee=_match_column(columns, FIELD_ALIASES["shipping_fee"]),
        total=_match_column(columns, FIELD_ALIASES["total"]),
        customer=_match_column(columns, FIELD_ALIASES["customer"]),
        prefecture=_match_column(columns, FIELD_ALIASES["prefecture"]),
        settlement=_match_column(columns, FIELD_ALIASES["settlement"]),
        status=_match_column(columns, FIELD_ALIASES["status"]),
        reference_code=_match_column(columns, FIELD_ALIASES["reference_code"]),
        month_tag=_match_column(columns, FIELD_ALIASES["month_tag"]),
    )


def _set_if(fields: dict[str, Any], col: str | None, val: Any) -> None:
    if col and val is not None and val != "":
        fields[col] = val


def build_records(
    orders: list[dict[str, Any]],
    columns: list[str],
    granularity: Granularity,
) -> list[tuple[dict[str, Any], dict[str, str]]]:
    """
    生成要写入 bitable 的 records。
    返回: [(fields, key_fields), ...]
      - fields: {"列名": 值}
      - key_fields: 用于 upsert 的唯一键字段（列名->值）
    """
    mapping = build_column_mapping(columns)

    order_no_col = mapping.order_number
    if not order_no_col:
        raise RuntimeError("目标表缺少 受注番号/注文番号 列，无法 upsert")

    out: list[tuple[dict[str, Any], dict[str, str]]] = []

    for o in orders:
        order_no = str(o.get("orderNumber") or "").strip()
        if not order_no:
            continue

        order_date_ms = _as_lark_datetime_ms(o.get("orderDatetime"))
        order_date_text = _as_date_str(o.get("orderDatetime"))
        month_tag_value = ""
        if order_date_text:
            try:
                month_tag_value = f"{int(order_date_text[5:7])}月"
            except Exception:
                month_tag_value = ""
        total = _num(o.get("requestPrice") or o.get("totalPrice"))
        shipping_fee = _num(o.get("shippingFee"))
        customer = str(o.get("ordererName") or "").strip()
        pref = str(o.get("ordererPrefecture") or "").strip()
        settlement = str(o.get("settlementMethod") or "").strip()
        status = str(o.get("orderStatus") or "").strip()
        reference_code_raw = o.get("orderProgressCode") or o.get("orderStatus") or ""
        reference_code_num = _int_or_none(reference_code_raw)

        if granularity == "per_order":
            fields: dict[str, Any] = {}
            _set_if(fields, mapping.order_number, order_no)
            _set_if(fields, mapping.order_date, order_date_ms)
            _set_if(fields, mapping.total, total)
            _set_if(fields, mapping.shipping_fee, shipping_fee)
            _set_if(fields, mapping.customer, customer)
            _set_if(fields, mapping.prefecture, pref)
            _set_if(fields, mapping.settlement, settlement)
            _set_if(fields, mapping.status, status)
            _set_if(fields, mapping.reference_code, reference_code_num)
            _set_if(fields, mapping.month_tag, month_tag_value)

            key_fields = {order_no_col: order_no}
            out.append((fields, key_fields))
            continue

        # per_item
        items = o.get("items") or []
        for it in items:
            sku = str(it.get("manageNumber") or it.get("itemNumber") or "").strip()
            system_sku = str(it.get("systemSku") or "").strip()
            item_name = str(it.get("itemName") or "").strip()
            qty = _int(it.get("units"))
            unit_price = _num(it.get("price"))
            subtotal = unit_price * qty

            fields = {}
            _set_if(fields, mapping.order_number, order_no)
            _set_if(fields, mapping.order_date, order_date_ms)
            _set_if(fields, mapping.sku, sku)
            # 仅在确实有系统连携 SKU 时写入，避免误把 SKU管理番号 覆盖过去
            _set_if(fields, mapping.system_sku, system_sku)
            _set_if(fields, mapping.item_name, item_name)
            _set_if(fields, mapping.qty, qty)
            _set_if(fields, mapping.unit_price, unit_price)
            _set_if(fields, mapping.subtotal, subtotal)
            _set_if(fields, mapping.total, total)
            _set_if(fields, mapping.shipping_fee, shipping_fee)
            _set_if(fields, mapping.customer, customer)
            _set_if(fields, mapping.prefecture, pref)
            _set_if(fields, mapping.settlement, settlement)
            _set_if(fields, mapping.status, status)
            _set_if(fields, mapping.reference_code, reference_code_num)
            _set_if(fields, mapping.month_tag, month_tag_value)

            key_fields: dict[str, str] = {order_no_col: order_no}
            # if table has sku column, use (order_no + sku) as unique key
            if mapping.sku:
                key_fields[mapping.sku] = sku
            elif mapping.item_name:
                key_fields[mapping.item_name] = item_name

            out.append((fields, key_fields))

    return out


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ",".join(_norm(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v.get("value") or v).strip()
    return str(v).strip()


def _norm_sku_token(value: str) -> str:
    return str(value or "").strip().upper().replace("　", " ")


def tag_spu_matches(system_sku: str, tag_spu: str) -> bool:
    system = _norm_sku_token(system_sku)
    tag = _norm_sku_token(tag_spu)
    if not system or not tag:
        return False
    if tag == system:
        return True
    separators = ["_", "-", "/", " ", ",", "，", ";", "；", "|"]
    return any(
        tag.startswith(system + sep) or
        tag.endswith(sep + system) or
        f"{sep}{system}{sep}" in f"{sep}{tag}{sep}"
        for sep in separators
    )


def find_matching_tag_spu(system_sku: str, spu_tags: set[str]) -> str:
    for tag in spu_tags:
        if tag_spu_matches(system_sku, tag):
            return tag
    return ""


def build_sync_warnings(
    pairs: list[tuple[dict[str, Any], dict[str, str]]],
    columns: list[str],
    plan: dict[str, Any],
    *,
    spu_tags: set[str] | None = None,
    spu_warning: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Create human-readable warnings from one Lark scan plus incoming Rakuten rows."""
    mapping = build_column_mapping(columns)
    existing_records = plan.get("existing_records") or []
    creates = plan.get("creates") or []

    existing_skus: set[str] = set()
    existing_order_skus: dict[str, set[str]] = {}
    for record in existing_records:
        fields = record.get("fields") or {}
        sku = _norm(fields.get(mapping.sku)) if mapping.sku else ""
        order_no = _norm(fields.get(mapping.order_number)) if mapping.order_number else ""
        if sku:
            existing_skus.add(sku)
        if order_no:
            existing_order_skus.setdefault(order_no, set())
            if sku:
                existing_order_skus[order_no].add(sku)

    blank_system_sku: list[dict[str, str]] = []
    missing_tag_spu: list[dict[str, str]] = []
    if mapping.system_sku:
        for fields, key_fields in pairs:
            system_sku = _norm(fields.get(mapping.system_sku))
            if system_sku:
                if spu_tags is not None and not find_matching_tag_spu(system_sku, spu_tags):
                    missing_tag_spu.append(
                        {
                            "order_number": _norm(key_fields.get(mapping.order_number)) if mapping.order_number else "",
                            "sku": _norm(fields.get(mapping.sku)) if mapping.sku else "",
                            "system_sku": system_sku,
                            "item_name": _norm(fields.get(mapping.item_name)) if mapping.item_name else "",
                        }
                    )
                continue
            blank_system_sku.append(
                {
                    "order_number": _norm(key_fields.get(mapping.order_number)) if mapping.order_number else "",
                    "sku": _norm(fields.get(mapping.sku)) if mapping.sku else "",
                    "item_name": _norm(fields.get(mapping.item_name)) if mapping.item_name else "",
                }
            )

    new_items: list[dict[str, str]] = []
    possible_sku_changes: list[dict[str, Any]] = []
    for item in creates:
        fields = item.get("fields") or {}
        key_fields = item.get("key_fields") or {}
        sku = _norm(fields.get(mapping.sku)) if mapping.sku else ""
        order_no = _norm(key_fields.get(mapping.order_number)) if mapping.order_number else ""
        item_name = _norm(fields.get(mapping.item_name)) if mapping.item_name else ""

        if sku and sku not in existing_skus:
            new_items.append({"order_number": order_no, "sku": sku, "item_name": item_name})

        known_skus_for_order = existing_order_skus.get(order_no) if order_no else None
        if known_skus_for_order and sku and sku not in known_skus_for_order:
            possible_sku_changes.append(
                {
                    "order_number": order_no,
                    "new_sku": sku,
                    "existing_skus": sorted(known_skus_for_order),
                    "item_name": item_name,
                }
            )

    duplicate_keys = plan.get("duplicate_keys") or []

    return {
        "new_items": {
            "count": len(new_items),
            "items": new_items[:limit],
        },
        "possible_sku_changes": {
            "count": len(possible_sku_changes),
            "items": possible_sku_changes[:limit],
        },
        "blank_system_sku": {
            "count": len(blank_system_sku),
            "items": blank_system_sku[:limit],
        },
        "missing_tag_spu": {
            "count": len(missing_tag_spu),
            "items": missing_tag_spu[:limit],
            "spu_warning": spu_warning,
        },
        "duplicate_keys": {
            "count": len(duplicate_keys),
            "items": duplicate_keys[:limit],
        },
    }

