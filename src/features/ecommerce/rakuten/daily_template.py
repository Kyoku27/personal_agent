from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import time as time_module
from typing import Any

import requests

from src.core.config_manager import get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token
from src.features.feishu.user_oauth import refresh_user_access_token
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable

JST = timezone(timedelta(hours=9))
DAILY_VIEW_NAMES = ["HRP", "MOFT", "CZUR", "Genki", "Hidock", "DB"]
DAILY_TEMPLATE_STORES = {
    "default": {"label": "EZLIFE", "wiki_env": "FEISHU_RAKUTEN_WIKI_NODE_TOKEN", "view_names": DAILY_VIEW_NAMES},
    "ezlife": {"label": "EZLIFE", "wiki_env": "FEISHU_RAKUTEN_WIKI_NODE_TOKEN", "view_names": DAILY_VIEW_NAMES},
    "store2": {"label": "tomtoc", "wiki_env": "FEISHU_RAKUTEN_STORE2_WIKI_NODE_TOKEN", "view_names": ["DB"]},
    "tomtoc": {"label": "tomtoc", "wiki_env": "FEISHU_RAKUTEN_STORE2_WIKI_NODE_TOKEN", "view_names": ["DB"]},
}


def _store_config(store_id: str | None) -> dict[str, Any]:
    normalized = (store_id or "default").strip().lower()
    config = DAILY_TEMPLATE_STORES.get(normalized)
    if not config:
        valid = ", ".join(sorted(DAILY_TEMPLATE_STORES))
        raise ValueError(f"Unsupported daily template store_id {store_id!r}. Use one of: {valid}")
    return config


def _resolve_daily_app_token(store_id: str | None) -> tuple[str, dict[str, Any]]:
    config = _store_config(store_id)
    wiki_node_token = get_env(config["wiki_env"], "") or ""
    if not wiki_node_token:
        raise RuntimeError(f"{config['wiki_env']} is not configured for {config['label']} daily template copy.")
    return resolve_wiki_to_bitable(wiki_node_token), config


def _parse_month(month: str) -> tuple[int, int]:
    try:
        dt = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must be YYYY-MM") from exc
    return dt.year, dt.month


def _month_start_ms(month: str) -> int:
    year, month_num = _parse_month(month)
    return int(datetime.combine(datetime(year, month_num, 1).date(), time.min, JST).timestamp() * 1000)


def _daily_table_name(month: str) -> str:
    _, month_num = _parse_month(month)
    return f"{month_num}\u6708_\u65e5\u5225"


def _auth_headers(prefer_user: bool = True) -> dict[str, str]:
    user_token = get_env("LARK_USER_ACCESS_TOKEN", "") or ""
    token = user_token if prefer_user and user_token else _get_tenant_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _lark_request(method: str, url: str, prefer_user: bool = True, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", None) or _auth_headers(prefer_user=prefer_user)
    resp = requests.request(method, url, headers=headers, **kwargs)
    if prefer_user and resp.status_code == 401 and "expired" in resp.text.lower():
        refresh_user_access_token()
        resp = requests.request(method, url, headers=_auth_headers(prefer_user=True), **kwargs)
    return resp


def _raise_lark_error(resp: requests.Response, action: str) -> None:
    text = resp.text
    if resp.status_code == 401 and "expired" in text.lower():
        raise RuntimeError(
            "Lark user token expired. Open /api/lark/oauth/url, authorize again, "
            "then retry the monthly template action."
        )
    raise RuntimeError(f"Lark API HTTP {resp.status_code} {action}: {text}")


def _is_copying_error(data: dict[str, Any] | None = None, text: str = "") -> bool:
    if data and data.get("code") == 1254036:
        return True
    return "copying" in text.lower() or "复制中" in text


def _extract_table_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") or {}
    candidates = [
        data,
        data.get("table") or {},
        data.get("item") or {},
        data.get("table_id") or {},
    ]
    for item in candidates:
        if isinstance(item, dict):
            table_id = item.get("table_id") or item.get("id")
            if table_id:
                return str(table_id)
        elif isinstance(item, str):
            return item
    return ""


def _list_tables(app_token: str, prefer_user: bool = True) -> list[dict[str, Any]]:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables"
    tables: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = _lark_request("GET", url, headers=headers, prefer_user=prefer_user, params=params, timeout=15)
        if not resp.ok:
            _raise_lark_error(resp, "GET tables")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark list tables failed: {data}")
        payload = data.get("data") or {}
        tables.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token") or ""
    return tables


def _list_fields(app_token: str, table_id: str, prefer_user: bool = True) -> list[dict[str, Any]]:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    fields: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 200}
        if page_token:
            params["page_token"] = page_token
        resp = _lark_request("GET", url, headers=headers, prefer_user=prefer_user, params=params, timeout=20)
        if not resp.ok:
            _raise_lark_error(resp, "GET fields")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark list fields failed: {data}")
        payload = data.get("data") or {}
        fields.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token") or ""
    return fields


def _list_views(app_token: str, table_id: str, prefer_user: bool = True) -> list[dict[str, Any]]:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/views"
    resp = _lark_request("GET", url, headers=headers, prefer_user=prefer_user, params={"page_size": 100}, timeout=20)
    if not resp.ok:
        _raise_lark_error(resp, "GET views")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark list views failed: {data}")
    return (data.get("data") or {}).get("items") or []


def _create_view(app_token: str, table_id: str, view_name: str, prefer_user: bool = True) -> None:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/views"
    resp = _lark_request("POST", url, headers=headers, prefer_user=prefer_user, json={"view_name": view_name, "view_type": "grid"}, timeout=20)
    if not resp.ok:
        _raise_lark_error(resp, f"create view {view_name}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark create view {view_name} failed: {data}")


def _ensure_daily_views(
    app_token: str,
    table_id: str,
    prefer_user: bool = True,
    view_names: list[str] | None = None,
) -> list[str]:
    views = _list_views(app_token, table_id, prefer_user=prefer_user)
    existing = {str(view.get("view_name") or "") for view in views}
    created: list[str] = []
    for view_name in view_names or DAILY_VIEW_NAMES:
        if view_name in existing:
            continue
        _create_view(app_token, table_id, view_name, prefer_user=prefer_user)
        created.append(view_name)
    return created


def _create_table(app_token: str, table_name: str, primary_field: dict[str, Any], prefer_user: bool = True) -> str:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables"
    field_payload = {
        "field_name": primary_field["field_name"],
        "type": primary_field["type"],
    }
    if primary_field.get("property"):
        field_payload["property"] = primary_field["property"]
    resp = _lark_request(
        "POST",
        url,
        headers=headers,
        prefer_user=prefer_user,
        json={"table": {"name": table_name, "default_view_name": "表格", "fields": [field_payload]}},
        timeout=30,
    )
    if not resp.ok:
        _raise_lark_error(resp, "create table")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark create table failed: {data}")
    return _extract_table_id(data)


def _create_field(app_token: str, table_id: str, field: dict[str, Any], prefer_user: bool = True) -> str:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    payload: dict[str, Any] = {
        "field_name": field["field_name"],
        "type": field["type"],
    }
    if field.get("property"):
        payload["property"] = field["property"]
    resp = _lark_request("POST", url, headers=headers, prefer_user=prefer_user, json=payload, timeout=30)
    if not resp.ok:
        _raise_lark_error(resp, f"create field {field.get('field_name')}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark create field {field.get('field_name')} failed: {data}")
    created = data.get("data") or {}
    return str(created.get("field_id") or (created.get("field") or {}).get("field_id") or "")


def _update_field(app_token: str, table_id: str, field_id: str, field: dict[str, Any], prefer_user: bool = True) -> None:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
    payload: dict[str, Any] = {
        "field_name": field["field_name"],
        "type": field["type"],
    }
    if field.get("property"):
        payload["property"] = field["property"]
    resp = _lark_request("PUT", url, headers=headers, prefer_user=prefer_user, json=payload, timeout=30)
    if not resp.ok:
        _raise_lark_error(resp, f"update field {field.get('field_name')}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark update field {field.get('field_name')} failed: {data}")


def _replace_ids(value: Any, table_id_map: dict[str, str], field_id_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in table_id_map.items():
            value = value.replace(old, new)
        for old, new in field_id_map.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_ids(item, table_id_map, field_id_map) for item in value]
    if isinstance(value, dict):
        return {key: _replace_ids(item, table_id_map, field_id_map) for key, item in value.items()}
    return value


def _field_create_payload(field: dict[str, Any], table_id_map: dict[str, str], field_id_map: dict[str, str]) -> dict[str, Any]:
    payload = {
        "field_name": field["field_name"],
        "type": field["type"],
    }
    if field.get("property"):
        payload["property"] = _sanitize_field_property(field, _replace_ids(field["property"], table_id_map, field_id_map))
    return payload


def _sanitize_field_property(field: dict[str, Any], property_value: dict[str, Any]) -> dict[str, Any]:
    field_type = field.get("type")
    if field_type == 3:
        return {
            "options": [
                {"name": option.get("name")}
                for option in property_value.get("options", [])
                if option.get("name")
            ]
        }
    if field_type == 5:
        return {"date_formatter": property_value.get("date_formatter", "yyyy/MM/dd")}
    return property_value


def _date_formula_field(field: dict[str, Any]) -> dict[str, Any]:
    name = str(field.get("field_name") or "")
    day = "".join(ch for ch in name.split("日付", 1)[0] if ch.isdigit())
    copied = {
        "field_id": field["field_id"],
        "field_name": field["field_name"],
        "type": field["type"],
        "property": dict(field.get("property") or {}),
    }
    if day:
        copied["property"]["formula_expression"] = f"DATE(YEAR([対象月]),MONTH([対象月]),{int(day)})"
    return copied


def _formula_field(field: dict[str, Any], target_month: str | None = None) -> dict[str, Any]:
    copied = {
        "field_id": field["field_id"],
        "field_name": field["field_name"],
        "type": field["type"],
        "property": dict(field.get("property") or {}),
    }
    formula = str(copied["property"].get("formula_expression") or "")
    if target_month and formula:
        year, month_num = _parse_month(target_month)
        lower_bound = (datetime(year, month_num, 1, tzinfo=JST) - timedelta(days=1)).strftime("%Y-%m-%d")
        if month_num == 12:
            upper_bound = datetime(year + 1, 1, 1, tzinfo=JST).strftime("%Y-%m-%d")
        else:
            upper_bound = datetime(year, month_num + 1, 1, tzinfo=JST).strftime("%Y-%m-%d")
        copied["property"]["formula_expression"] = _replace_date_bounds(formula, lower_bound, upper_bound)
    return copied


def _formula_placeholder_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "field_id": field["field_id"],
        "field_name": field["field_name"],
        "type": 20,
        "property": {"formula_expression": "0"},
    }


def _lookup_formula_field(field: dict[str, Any], target_month: str | None = None) -> dict[str, Any]:
    source_property = field.get("property") or {}
    formula = source_property.get("formula", "")
    if target_month and field.get("field_name") in {"合計", "金額"}:
        year, month_num = _parse_month(target_month)
        start = datetime(year, month_num, 1, tzinfo=JST) - timedelta(days=1)
        if month_num == 12:
            next_start = datetime(year + 1, 1, 1, tzinfo=JST)
        else:
            next_start = datetime(year, month_num + 1, 1, tzinfo=JST)
        formula = _replace_date_bounds(formula, start.strftime("%Y-%m-%d"), next_start.strftime("%Y-%m-%d"))
    property_value: dict[str, Any] = {
        "formula_expression": formula,
    }
    return {
        "field_id": field["field_id"],
        "field_name": field["field_name"],
        "type": 20,
        "property": property_value,
    }


def _replace_date_bounds(formula: str, lower_bound: str, upper_bound: str) -> str:
    import re

    dates = re.findall(r'TODATE\("(\d{4}-\d{2}-\d{2})"\)', formula)
    if len(dates) < 2:
        return formula
    formula = formula.replace(f'TODATE("{dates[0]}")', f'TODATE("{lower_bound}")', 1)
    return formula.replace(f'TODATE("{dates[1]}")', f'TODATE("{upper_bound}")', 1)


def _manual_clone_daily_table(
    app_token: str,
    source_table_id: str,
    target_name: str,
    target_month: str,
    prefer_user: bool = True,
) -> tuple[str, dict[str, str], list[str]]:
    source_fields = _list_fields(app_token, source_table_id, prefer_user=prefer_user)
    primary = next((field for field in source_fields if field.get("is_primary")), source_fields[0])
    target_table_id = _create_table(app_token, target_name, primary, prefer_user=prefer_user)
    target_fields = _list_fields(app_token, target_table_id, prefer_user=prefer_user)
    field_id_map: dict[str, str] = {}
    created_fields: list[str] = []

    target_primary = next((field for field in target_fields if field.get("is_primary")), target_fields[0])
    field_id_map[str(primary["field_id"])] = str(target_primary["field_id"])
    table_id_map = {source_table_id: target_table_id}

    pending_formula_updates: list[tuple[str, dict[str, Any]]] = []
    for source_field in source_fields:
        if source_field.get("is_primary"):
            continue
        if source_field.get("type") == 19:
            field = _formula_placeholder_field(source_field)
            final_field = _lookup_formula_field(source_field, target_month=target_month)
        elif source_field.get("type") == 20:
            field = _formula_placeholder_field(source_field)
            field_name = str(source_field.get("field_name") or "")
            final_field = (
                _date_formula_field(source_field)
                if field_name.endswith("日付")
                else _formula_field(source_field, target_month=target_month)
            )
        else:
            field = source_field
            final_field = source_field
        created_id = _create_field(
            app_token,
            target_table_id,
            _field_create_payload(field, table_id_map, field_id_map),
            prefer_user=prefer_user,
        )
        if created_id:
            field_id_map[str(source_field["field_id"])] = created_id
            if source_field.get("type") in {19, 20}:
                pending_formula_updates.append((created_id, final_field))
        created_fields.append(str(source_field["field_name"]))

    for field_id, field in pending_formula_updates:
        _update_field(
            app_token,
            target_table_id,
            field_id,
            _field_create_payload(field, table_id_map, field_id_map),
            prefer_user=prefer_user,
        )

    return target_table_id, field_id_map, created_fields


def _rename_table(app_token: str, table_id: str, name: str, prefer_user: bool = True) -> None:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}"
    last_error: dict[str, Any] | None = None
    for _ in range(10):
        resp = _lark_request("PATCH", url, headers=headers, prefer_user=prefer_user, json={"name": name}, timeout=15)
        if not resp.ok:
            if _is_copying_error(text=resp.text):
                time_module.sleep(1)
                continue
            _raise_lark_error(resp, "rename table")
        data = resp.json()
        if data.get("code") == 0:
            return
        if _is_copying_error(data=data):
            last_error = data
            time_module.sleep(1)
            continue
        raise RuntimeError(f"Lark rename table failed: {data}")
    raise RuntimeError(f"Lark rename table failed after waiting for copy to finish: {last_error}")


def _find_new_copied_table(
    app_token: str,
    before_ids: set[str],
    source_name: str,
    prefer_user: bool = True,
) -> dict[str, Any] | None:
    for _ in range(10):
        tables = _list_tables(app_token, prefer_user=prefer_user)
        new_tables = [t for t in tables if str(t.get("table_id") or "") not in before_ids]
        if new_tables:
            return new_tables[0]
        copied = [
            t for t in tables
            if str(t.get("name") or "").startswith(source_name) and str(t.get("table_id") or "") not in before_ids
        ]
        if copied:
            return copied[0]
        time_module.sleep(1)
    return None


def _list_records(app_token: str, table_id: str, prefer_user: bool = True) -> list[dict[str, Any]]:
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    records: list[dict[str, Any]] = []
    page_token = ""
    copy_waits = 0
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = _lark_request("GET", url, headers=headers, prefer_user=prefer_user, params=params, timeout=20)
        if not resp.ok:
            if _is_copying_error(text=resp.text) and copy_waits < 10:
                copy_waits += 1
                time_module.sleep(1)
                continue
            _raise_lark_error(resp, "GET records")
        data = resp.json()
        if _is_copying_error(data=data) and copy_waits < 10:
            copy_waits += 1
            time_module.sleep(1)
            continue
        if data.get("code") != 0:
            raise RuntimeError(f"Lark list records failed: {data}")
        payload = data.get("data") or {}
        records.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token") or ""
    return records


def _batch_update_records(app_token: str, table_id: str, rows: list[dict[str, Any]], prefer_user: bool = True) -> None:
    if not rows:
        return
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
    for i in range(0, len(rows), 500):
        resp = _lark_request("POST", url, headers=headers, prefer_user=prefer_user, json={"records": rows[i:i + 500]}, timeout=30)
        if not resp.ok:
            _raise_lark_error(resp, "batch_update")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark batch_update failed: {data}")


def _batch_create_records(app_token: str, table_id: str, rows: list[dict[str, Any]], prefer_user: bool = True) -> None:
    if not rows:
        return
    headers = _auth_headers(prefer_user=prefer_user)
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    for i in range(0, len(rows), 500):
        resp = _lark_request("POST", url, headers=headers, prefer_user=prefer_user, json={"records": [{"fields": fields} for fields in rows[i:i + 500]]}, timeout=30)
        if not resp.ok:
            _raise_lark_error(resp, "batch_create")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark batch_create failed: {data}")


def _normalize_record_value_for_write(value: Any) -> Any:
    if isinstance(value, list) and all(isinstance(item, dict) and "text" in item for item in value):
        return "".join(str(item.get("text") or "") for item in value)
    return value


def _copy_daily_records(
    app_token: str,
    source_table_id: str,
    target_table_id: str,
    source_fields: list[dict[str, Any]],
    target_month_value: int,
    prefer_user: bool = True,
) -> int:
    writable_field_names = {
        field["field_name"]
        for field in source_fields
        if field.get("type") not in {19, 20}
    }
    source_records = _list_records(app_token, source_table_id, prefer_user=prefer_user)
    create_rows: list[dict[str, Any]] = []
    for record in source_records:
        fields = record.get("fields") or {}
        row = {
            name: _normalize_record_value_for_write(value)
            for name, value in fields.items()
            if name in writable_field_names
        }
        row["対象月"] = target_month_value
        if any(value not in ("", None, []) for value in row.values()):
            create_rows.append(row)
    _batch_create_records(app_token, target_table_id, create_rows, prefer_user=prefer_user)
    return len(create_rows)


def _field_names_in_order(fields: list[dict[str, Any]]) -> list[str]:
    return [str(field.get("field_name") or "") for field in fields]


def _daily_layout_matches(source_fields: list[dict[str, Any]], target_fields: list[dict[str, Any]]) -> bool:
    return _field_names_in_order(source_fields) == _field_names_in_order(target_fields)


def inspect_daily_tables(store_id: str | None = None) -> dict[str, Any]:
    app_token, store_config = _resolve_daily_app_token(store_id)
    store_label = str(store_config["label"])
    tables = _list_tables(app_token, prefer_user=True)
    return {
        "success": True,
        "store_label": store_label,
        "table_count": len(tables),
        "tables": [{"name": t.get("name"), "table_id": t.get("table_id")} for t in tables],
    }


def prepare_daily_template(
    source_month: str,
    target_month: str,
    dry_run: bool = False,
    store_id: str | None = None,
) -> dict[str, Any]:
    app_token, store_config = _resolve_daily_app_token(store_id)
    store_label = str(store_config["label"])
    view_names = list(store_config.get("view_names") or DAILY_VIEW_NAMES)
    source_name = _daily_table_name(source_month)
    target_name = _daily_table_name(target_month)
    tables = _list_tables(app_token, prefer_user=True)
    source = next((t for t in tables if t.get("name") == source_name), None)
    target = next((t for t in tables if t.get("name") == target_name), None)
    copied_from_source = False

    if not source:
        return {
            "success": False,
            "store_label": store_label,
            "message": f"{store_label} source table {source_name} was not found with current Lark user token.",
            "visible_tables": [t.get("name") for t in tables],
        }

    source_fields = _list_fields(app_token, source["table_id"], prefer_user=True)
    if target:
        target_fields = _list_fields(app_token, target["table_id"], prefer_user=True)
        if not _daily_layout_matches(source_fields, target_fields):
            partial_name = f"{target_name}_partial_{datetime.now(JST).strftime('%Y%m%d%H%M%S')}"
            _rename_table(app_token, target["table_id"], partial_name, prefer_user=True)
            target = None

    if not target:
        if dry_run:
            source_count = len(_list_records(app_token, source["table_id"], prefer_user=True))
            return {
                "success": True,
                "store_label": store_label,
                "message": f"Would create {store_label} {target_name} from {source_name}, copy {len(source_fields)} fields, and write {source_count} records",
                "source_table_id": source.get("table_id"),
                "record_count": source_count,
                "field_count": len(source_fields),
                "dry_run": True,
            }

        target_month_value = _month_start_ms(target_month)
        target_table_id, _, created_fields = _manual_clone_daily_table(
            app_token,
            source["table_id"],
            target_name,
            target_month,
            prefer_user=True,
        )
        copied_from_source = True
        record_count = _copy_daily_records(
            app_token,
            source["table_id"],
            target_table_id,
            source_fields,
            target_month_value,
            prefer_user=True,
        )
        created_views = _ensure_daily_views(app_token, target_table_id, prefer_user=True, view_names=view_names)
        return {
            "success": True,
            "store_label": store_label,
            "message": f"Created {store_label} {target_name} from {source_name}: copied {len(created_fields) + 1} fields, {record_count} records, and created {len(created_views)} views",
            "source_table_id": source.get("table_id"),
            "target_table_id": target_table_id,
            "record_count": record_count,
            "field_count": len(created_fields) + 1,
            "created_views": created_views,
            "dry_run": dry_run,
        }

    table_id = target.get("table_id")
    records = _list_records(app_token, table_id, prefer_user=True)
    target_month_value = _month_start_ms(target_month)
    update_rows = [
        {"record_id": item["record_id"], "fields": {"\u5bfe\u8c61\u6708": target_month_value}}
        for item in records
        if item.get("record_id")
    ]
    if not dry_run:
        _batch_update_records(app_token, table_id, update_rows, prefer_user=True)
        created_views = _ensure_daily_views(app_token, table_id, prefer_user=True, view_names=view_names)
    else:
        created_views = []
    return {
        "success": True,
        "store_label": store_label,
        "message": (
            f"Prepared {store_label} {target_name}: "
            f"{'copied from ' + source_name + ' and ' if copied_from_source else ''}"
            f"target month updated for {len(update_rows)} records"
        ),
        "source_table_id": source.get("table_id"),
        "target_table_id": table_id,
        "record_count": len(update_rows),
        "created_views": created_views,
        "dry_run": dry_run,
    }
