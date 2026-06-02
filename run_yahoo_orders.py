from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from src.core.config_manager import BASE_DIR, get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token
from src.features.feishu.user_oauth import refresh_user_access_token
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable


JST = timezone(timedelta(hours=9))
YAHOO_ALL_SKU_TABLE_ID = "tblggAk2fZYAnyHe"
YAHOO_ORDER_DETAIL_TABLE_ID = "tblNfz8YgwliesNg"


def _set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        next_lines.append(f"{key}={value}")
    path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def _required_env(name: str) -> str:
    value = get_env(name, "") or ""
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def refresh_yahoo_access_token() -> str:
    client_id = _required_env("YAHOO_CLIENT_ID")
    client_secret = _required_env("YAHOO_CLIENT_SECRET")
    refresh_token = _required_env("YAHOO_REFRESH_TOKEN")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    resp = requests.post(
        "https://auth.login.yahoo.co.jp/yconnect/v2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    access_token = data.get("access_token") or ""
    if not access_token:
        raise RuntimeError(f"Yahoo token refresh did not return access_token: {data}")
    _set_env_value(BASE_DIR / ".env", "YAHOO_ACCESS_TOKEN", access_token)
    next_refresh = data.get("refresh_token") or ""
    if next_refresh:
        _set_env_value(BASE_DIR / ".env", "YAHOO_REFRESH_TOKEN", next_refresh)
    return access_token


def _yahoo_headers() -> dict[str, str]:
    token = get_env("YAHOO_ACCESS_TOKEN", "") or refresh_yahoo_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/xml; charset=UTF-8",
    }
    public_key = get_env("YAHOO_PUBLIC_KEY", "") or ""
    public_key_version = get_env("YAHOO_PUBLIC_KEY_VERSION", "") or ""
    seller_id = get_env("YAHOO_SELLER_ID", "") or ""
    if public_key and public_key_version and seller_id:
        headers.update(_yahoo_public_key_headers(public_key, public_key_version, seller_id))
    return headers


def _yahoo_public_key_headers(public_key_text: str, public_key_version: str, seller_id: str) -> dict[str, str]:
    key_text = public_key_text.replace("\\n", "\n")
    if "BEGIN PUBLIC KEY" not in key_text:
        key_text = f"-----BEGIN PUBLIC KEY-----\n{key_text}\n-----END PUBLIC KEY-----\n"
    key_bytes = key_text.encode("utf-8")
    public_key = serialization.load_pem_public_key(key_bytes)
    auth_value = f"{seller_id}:{int(time.time())}".encode("utf-8")
    encrypted = public_key.encrypt(auth_value, padding.PKCS1v15())
    return {
        "X-sws-signature": base64.b64encode(encrypted).decode("ascii"),
        "X-sws-signature-version": str(public_key_version),
    }


def _post_yahoo_xml(path: str, xml_body: str) -> ET.Element:
    base = (get_env("YAHOO_ORDER_API_BASE", "") or "https://circus.shopping.yahooapis.jp/ShoppingWebService/V1").rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    resp = requests.post(url, headers=_yahoo_headers(), data=xml_body.encode("utf-8"), timeout=60)
    if resp.status_code in (401, 403):
        refresh_yahoo_access_token()
        resp = requests.post(url, headers=_yahoo_headers(), data=xml_body.encode("utf-8"), timeout=60)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    status = _text(root, ".//Status")
    if status and status.upper() != "OK":
        code = _text(root, ".//Error/Code")
        msg = _text(root, ".//Error/Message")
        raise RuntimeError(f"Yahoo API {path} failed: {code} {msg}")
    return root


def _text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    found = node.find(path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def _children_text(node: ET.Element | None, name: str) -> list[str]:
    if node is None:
        return []
    return [child.text.strip() for child in node.findall(name) if child.text and child.text.strip()]


def _compact_xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def yahoo_order_list(start_date: str, end_date: str) -> list[str]:
    seller_id = _required_env("YAHOO_SELLER_ID")
    # Yahoo expects YYYYMMDDHHMMSS-like timestamps for order search conditions.
    start = start_date.replace("-", "") + "000000"
    end = end_date.replace("-", "") + "235959"
    order_ids: list[str] = []
    start_index = 1
    page_size = 100
    while True:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Req>
  <Search>
    <Condition>
      <OrderTimeFrom>{start}</OrderTimeFrom>
      <OrderTimeTo>{end}</OrderTimeTo>
    </Condition>
    <Field>OrderId,OrderTime,TotalPrice</Field>
    <Result>{page_size}</Result>
    <Start>{start_index}</Start>
  </Search>
  <SellerId>{_compact_xml_text(seller_id)}</SellerId>
</Req>"""
        root = _post_yahoo_xml("orderList", body)
        page_ids = [_text(item, "OrderId") for item in root.findall(".//OrderInfo")]
        page_ids = [item for item in page_ids if item]
        order_ids.extend(page_ids)
        total = int(_text(root, ".//TotalCount", "0") or 0)
        if len(order_ids) >= total or not page_ids:
            break
        start_index += page_size
    return sorted(set(order_ids))


def yahoo_order_info(order_id: str) -> dict[str, Any]:
    seller_id = _required_env("YAHOO_SELLER_ID")
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Req>
  <Target>
    <OrderId>{_compact_xml_text(order_id)}</OrderId>
    <Field>OrderId,OrderTime,TotalPrice,OrderStatus,PayStatus,ShipStatus,ItemId,Title,SubCode,SubCodeOption,ItemOption,UnitPrice,Quantity,LineId</Field>
  </Target>
  <SellerId>{_compact_xml_text(seller_id)}</SellerId>
</Req>"""
    root = _post_yahoo_xml("orderInfo", body)
    order = root.find(".//OrderInfo") or root
    return {
        "order_id": _text(order, ".//OrderId") or order_id,
        "order_time": _text(order, ".//OrderTime"),
        "total_price": _text(order, ".//TotalPrice"),
        "status": _text(order, ".//OrderStatus"),
        "items": [_parse_item(item) for item in order.findall(".//Item")],
    }


def _parse_item(item: ET.Element) -> dict[str, Any]:
    options: list[str] = []
    for opt in item.findall("ItemOption"):
        name = _text(opt, "Name")
        value = _text(opt, "Value")
        if name or value:
            options.append(f"{name}:{value}" if name else value)
    sub_option = _text(item, "SubCodeOption")
    if sub_option:
        options.append(sub_option)
    return {
        "line_id": _text(item, "LineId"),
        "item_id": _text(item, "ItemId"),
        "title": _text(item, "Title"),
        "sub_code": _text(item, "SubCode"),
        "item_options": " / ".join(options),
        "unit_price": _to_int(_text(item, "UnitPrice")),
        "quantity": _to_int(_text(item, "Quantity")) or 1,
    }


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _brand_guess(text: str) -> str:
    if re.search(r"tomtoc", text, re.I):
        return "tomtoc"
    if re.search(r"MOFT|MOVAS|Magsafe|MagSafe", text):
        return "MOFT"
    if re.search(r"Homerunpet|homerun|Drybo|ペット|WF20|BF10|CF20", text, re.I):
        return "Homerunpet"
    if re.search(r"CZUR|Aura|ET24|Shine", text, re.I):
        return "CZUR"
    if re.search(r"HiDock", text, re.I):
        return "HiDock"
    if re.search(r"Nintendo|Switch|Genki", text, re.I):
        return "Nintendo Switch"
    return "UNKNOWN"


def _jst_date_ms(value: str) -> int | None:
    if not value:
        return None
    normalized = value.replace("+09:00", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(normalized, fmt).replace(tzinfo=JST)
            return int(datetime.combine(dt.date(), datetime.min.time(), JST).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _lark_headers(prefer_user: bool = True) -> dict[str, str]:
    token = get_env("LARK_USER_ACCESS_TOKEN", "") if prefer_user else ""
    if not token:
        token = _get_tenant_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}


def _lark_request(method: str, url: str, *, json_body: dict[str, Any] | None = None) -> requests.Response:
    resp = requests.request(method, url, headers=_lark_headers(True), json=json_body, timeout=60)
    if resp.status_code == 401:
        refresh_user_access_token()
        resp = requests.request(method, url, headers=_lark_headers(True), json=json_body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark API failed: {data}")
    return resp


def _list_records(app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=500"
        if page_token:
            url += f"&page_token={page_token}"
        data = _lark_request("GET", url).json().get("data") or {}
        records.extend(data.get("items") or [])
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token") or ""


def _batch_create(app_token: str, table_id: str, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), 500):
        _lark_request(
            "POST",
            f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            json_body={"records": [{"fields": row} for row in rows[i : i + 500]]},
        )


def _batch_update(app_token: str, table_id: str, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), 500):
        _lark_request(
            "POST",
            f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
            json_body={"records": rows[i : i + 500]},
        )


def sync_yahoo_orders(start_date: str | None = None, end_date: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    today = datetime.now(JST).date().isoformat()
    start_date = start_date or today
    end_date = end_date or start_date
    app_token = resolve_wiki_to_bitable(node_token=_required_env("FEISHU_YAHOO_WIKI_NODE_TOKEN"))

    existing_skus = _list_records(app_token, YAHOO_ALL_SKU_TABLE_ID)
    sku_by_code = {
        str((record.get("fields") or {}).get("sku_code") or ""): record
        for record in existing_skus
        if (record.get("fields") or {}).get("sku_code")
    }
    order_rows: list[dict[str, Any]] = []
    new_sku_rows: list[dict[str, Any]] = []
    order_ids = yahoo_order_list(start_date, end_date)
    for order_id in order_ids:
        order = yahoo_order_info(order_id)
        order_day_ms = _jst_date_ms(order.get("order_time") or "")
        month = ""
        if order_day_ms:
            month = f"{datetime.fromtimestamp(order_day_ms / 1000, JST).month}月"
        for item in order["items"]:
            sku_code = item["sub_code"] or f"SKU-TBD-{item['item_id']}"
            existing = sku_by_code.get(sku_code)
            if existing:
                sku_fields = existing.get("fields") or {}
                status = "matched_all_sku"
            else:
                brand = _brand_guess(" ".join([item["title"], item["item_id"], item["item_options"]]))
                sku_fields = {
                    "sku_code": sku_code,
                    "sku_manage_code": item["sub_code"],
                    "yahoo_item_code": item["item_id"],
                    "parent_product_name": item["title"],
                    "brand_suggest": brand,
                    "option_label": item["item_options"],
                    "source": "yahoo_order_api_unmatched",
                    "sku_status": "needs_mapping_from_yahoo_order",
                    "manual_sku_needed": "yes",
                    "needs_mapping": "yes",
                    "tag_brand": brand,
                    "tag_spu": item["item_id"],
                    "order_sample_name": item["title"],
                    "last_order_no": order["order_id"],
                    "match_note": "Appeared in Yahoo order API but not matched to Yahoo_All_SKU.",
                }
                new_sku_rows.append(sku_fields)
                sku_by_code[sku_code] = {"fields": sku_fields}
                status = "added_to_all_sku_needs_mapping"
            qty = item["quantity"]
            amount = item["unit_price"] * qty
            order_rows.append(
                {
                    "order_line_key": f"{order['order_id']}|{item['line_id']}|{sku_code}",
                    "order_number": order["order_id"],
                    "order_date": order_day_ms,
                    "month": month,
                    "sku_code": sku_code,
                    "sku_manage_code": item["sub_code"],
                    "system_sku": item["sub_code"],
                    "yahoo_item_code": item["item_id"],
                    "product_name": item["title"],
                    "item_options": item["item_options"],
                    "qty": qty,
                    "unit_price": item["unit_price"],
                    "line_amount": amount,
                    "db_total": amount,
                    "brand_suggest": sku_fields.get("brand_suggest", ""),
                    "tag_brand": sku_fields.get("tag_brand", ""),
                    "tag_spu": sku_fields.get("tag_spu", ""),
                    "sku_match_status": status,
                    "source_table": "Yahoo Order API",
                    "source_record_id": "",
                }
            )

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "order_ids": len(order_ids),
            "order_rows": len(order_rows),
            "new_skus": len(new_sku_rows),
            "preview": order_rows[:5],
        }

    existing_order_records = _list_records(app_token, YAHOO_ORDER_DETAIL_TABLE_ID)
    existing_order_by_key = {
        str((record.get("fields") or {}).get("order_line_key") or ""): record
        for record in existing_order_records
        if (record.get("fields") or {}).get("order_line_key")
    }
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for row in order_rows:
        existing = existing_order_by_key.get(row["order_line_key"])
        if existing:
            updates.append({"record_id": existing["record_id"], "fields": row})
        else:
            creates.append(row)
    _batch_create(app_token, YAHOO_ALL_SKU_TABLE_ID, new_sku_rows)
    _batch_create(app_token, YAHOO_ORDER_DETAIL_TABLE_ID, creates)
    _batch_update(app_token, YAHOO_ORDER_DETAIL_TABLE_ID, updates)
    return {
        "success": True,
        "start_date": start_date,
        "end_date": end_date,
        "order_ids": len(order_ids),
        "created_order_rows": len(creates),
        "updated_order_rows": len(updates),
        "new_skus_added": len(new_sku_rows),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync_yahoo_orders(args.start_date, args.end_date, args.dry_run), ensure_ascii=False, indent=2))
