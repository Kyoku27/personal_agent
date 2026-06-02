from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests

from src.core.config_manager import get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token

TARGET_TABLE_NAME = "年間_ダッシュボード_グラフ用"
MONTHS = [f"{i}月" for i in range(1, 13)]
BRANDS = ["HRP", "CZUR", "MOFT", "Genki", "HiDock"]


def _required_env(name: str) -> str:
    value = get_env(name, "") or ""
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


@dataclass(frozen=True)
class StoreConfig:
    store_id: str
    label: str
    app_token: str
    annual_target_table: str | None
    spu_monthly_table: str
    order_detail_table: str
    amount_fields: tuple[str, ...]


STORE_CONFIGS: dict[str, StoreConfig] = {
    "default": StoreConfig(
        store_id="default",
        label="ezlife",
        app_token=get_env("FEISHU_DASHBOARD_EZLIFE_APP_TOKEN", "") or "",
        annual_target_table="tblO4uMhFKRB98OB",
        spu_monthly_table="tblqRrpKDvVCrsRq",
        order_detail_table="tblcXa4MX8G0MHIT",
        amount_fields=("DB用合計", "合計金額（折后）", "合計金額"),
    ),
    "ezlife": StoreConfig(
        store_id="default",
        label="ezlife",
        app_token=get_env("FEISHU_DASHBOARD_EZLIFE_APP_TOKEN", "") or "",
        annual_target_table="tblO4uMhFKRB98OB",
        spu_monthly_table="tblqRrpKDvVCrsRq",
        order_detail_table="tblcXa4MX8G0MHIT",
        amount_fields=("DB用合計", "合計金額（折后）", "合計金額"),
    ),
    "store2": StoreConfig(
        store_id="store2",
        label="tomtoc",
        app_token=get_env("FEISHU_DASHBOARD_TOMTOC_APP_TOKEN", "") or "",
        annual_target_table=None,
        spu_monthly_table="tblUjdzC0K7cwygd",
        order_detail_table="tblNguPq4N3O6P4H",
        amount_fields=("DB用合計", "合計金額", "合計金額（折后）"),
    ),
    "tomtoc": StoreConfig(
        store_id="store2",
        label="tomtoc",
        app_token=get_env("FEISHU_DASHBOARD_TOMTOC_APP_TOKEN", "") or "",
        annual_target_table=None,
        spu_monthly_table="tblUjdzC0K7cwygd",
        order_detail_table="tblNguPq4N3O6P4H",
        amount_fields=("DB用合計", "合計金額", "合計金額（折后）"),
    ),
}


def _store_config(store_id: str | None) -> StoreConfig:
    key = (store_id or "default").lower()
    if key not in STORE_CONFIGS:
        raise ValueError(f"Unsupported dashboard store_id: {store_id}")
    config = STORE_CONFIGS[key]
    if not config.app_token:
        env_name = "FEISHU_DASHBOARD_TOMTOC_APP_TOKEN" if config.store_id == "store2" else "FEISHU_DASHBOARD_EZLIFE_APP_TOKEN"
        _required_env(env_name)
    return config


def _headers() -> dict[str, str]:
    user_token = get_env("LARK_USER_ACCESS_TOKEN", "") or ""
    token = user_token or _get_tenant_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}


def _get(app_token: str, path: str, **params: Any) -> dict[str, Any]:
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}{path}"
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"GET {url} HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"GET {url} failed: {data}")
    return data


def _post(app_token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{FEISHU_BASE_URL}/bitable/v1/apps/{app_token}{path}"
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"POST {url} HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"POST {url} failed: {data}")
    return data


def _records(config: StoreConfig, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = _get(config.app_token, f"/tables/{table_id}/records", **params)
        payload = data.get("data") or {}
        records.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token") or ""
    return records


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, list):
        return _number(value[0] if value else None)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return str(value[0].get("text") or value[0].get("name") or value[0].get("value") or "")
        return str(value[0]) if value else ""
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    return str(value)


def _amount(record: dict[str, Any], config: StoreConfig) -> float:
    for field in config.amount_fields:
        value = _number(record.get(field))
        if value:
            return value
    return 0.0


def _field(name: str, typ: int) -> dict[str, Any]:
    return {"field_name": name, "type": typ}


TARGET_FIELDS = [
    _field("キー", 1),
    _field("店舗", 1),
    _field("グラフ", 1),
    _field("月", 1),
    _field("指標", 1),
    _field("ブランド", 1),
    _field("SPU", 1),
    _field("値", 2),
    _field("金額", 2),
    _field("数量", 2),
    _field("達成率", 2),
    _field("順位", 2),
    _field("補足", 1),
]


def _ensure_target_fields(config: StoreConfig, table_id: str) -> list[str]:
    data = _get(config.app_token, f"/tables/{table_id}/fields", page_size=200)
    existing = {field.get("field_name") for field in (data.get("data") or {}).get("items") or []}
    created: list[str] = []
    for field in TARGET_FIELDS:
        name = field["field_name"]
        if name in existing:
            continue
        _post(config.app_token, f"/tables/{table_id}/fields", field)
        created.append(name)
    return created


def _ensure_target_table(config: StoreConfig) -> str:
    tables = (_get(config.app_token, "/tables", page_size=100).get("data") or {}).get("items") or []
    for table in tables:
        if table.get("name") == TARGET_TABLE_NAME:
            table_id = str(table["table_id"])
            _ensure_target_fields(config, table_id)
            return table_id

    data = _post(
        config.app_token,
        "/tables",
        {"table": {"name": TARGET_TABLE_NAME, "default_view_name": "グラフ用データ", "fields": TARGET_FIELDS}},
    )
    table = (data.get("data") or {}).get("table") or {}
    return str(table.get("table_id") or (data.get("data") or {}).get("table_id"))


def _base(config: StoreConfig, key: str, chart: str) -> dict[str, Any]:
    return {"キー": f"{config.label}|{key}", "店舗": config.label, "グラフ": chart}


def _build_rows(config: StoreConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if config.annual_target_table:
        annual_records = [r.get("fields") or {} for r in _records(config, config.annual_target_table)]
        for record in annual_records:
            month = _text(record.get("月別"))
            if not month:
                continue
            sales = _number(record.get("売上_実績"))
            target = _number(record.get("売上目標"))
            achievement = _number(record.get("達成率"))
            rows.append({
                **_base(config, f"monthly_goal|{month}|actual", "月次 売上 vs 目標"),
                "月": month,
                "指標": "売上実績",
                "値": sales,
                "金額": sales,
                "達成率": achievement,
            })
            rows.append({
                **_base(config, f"monthly_goal|{month}|target", "月次 売上 vs 目標"),
                "月": month,
                "指標": "売上目標",
                "値": target,
                "金額": target,
                "達成率": achievement,
            })
            for brand in BRANDS:
                amount = _number(record.get(f"{brand}_実績"))
                if amount:
                    rows.append({
                        **_base(config, f"brand_monthly|{month}|{brand}", "ブランド別 月次売上"),
                        "月": month,
                        "ブランド": brand,
                        "指標": "売上実績",
                        "値": amount,
                        "金額": amount,
                    })

    spu_records = [r.get("fields") or {} for r in _records(config, config.spu_monthly_table)]
    spu_totals: list[tuple[str, str, float, float]] = []
    month_totals: dict[str, dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "units": 0.0})
    for record in spu_records:
        spu = _text(record.get("SPU"))
        if not spu:
            continue
        amount = 0.0
        units = 0.0
        for month in MONTHS:
            month_amount = _number(record.get(f"{month}_金額"))
            month_units = _number(record.get(f"{month}合計"))
            amount += month_amount
            units += month_units
            month_totals[month]["amount"] += month_amount
            month_totals[month]["units"] += month_units
        brand = _text(record.get("Tag2HRP") or record.get("ダッシュボード用TAG"))
        if amount or units:
            spu_totals.append((spu, brand, amount, units))

    if not config.annual_target_table:
        for month in MONTHS:
            metrics = month_totals[month]
            if metrics["amount"] or metrics["units"]:
                rows.append({
                    **_base(config, f"monthly_sales|{month}", "月次 売上"),
                    "月": month,
                    "指標": "売上実績",
                    "値": metrics["amount"],
                    "金額": metrics["amount"],
                    "数量": metrics["units"],
                })

    for rank, (spu, brand, amount, units) in enumerate(sorted(spu_totals, key=lambda item: item[2], reverse=True)[:10], 1):
        rows.append({
            **_base(config, f"spu_top10|amount|{rank}|{spu}", "SPU 年間売上 TOP10"),
            "SPU": spu,
            "ブランド": brand,
            "指標": "年間売上",
            "値": amount,
            "金額": amount,
            "数量": units,
            "順位": rank,
        })

    detail_records = [r.get("fields") or {} for r in _records(config, config.order_detail_table)]
    profile_sales: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "units": 0.0, "orders": 0.0})
    hour_sales: dict[str, dict[str, float]] = defaultdict(lambda: {"sales": 0.0, "units": 0.0, "orders": 0.0})
    for record in detail_records:
        age = _text(record.get("年齢段"))
        sex = _text(record.get("性別"))
        hour = _text(record.get("購入時"))
        amount = _amount(record, config)
        units = _number(record.get("個数"))
        if age or sex:
            key = (age or "不明", sex or "不明")
            profile_sales[key]["sales"] += amount
            profile_sales[key]["units"] += units
            profile_sales[key]["orders"] += 1
        if hour:
            hour_sales[hour]["sales"] += amount
            hour_sales[hour]["units"] += units
            hour_sales[hour]["orders"] += 1
    for (age, sex), metrics in profile_sales.items():
        rows.append({
            **_base(config, f"profile_age_sex|{age}|{sex}", "年齢段 x 性別 売上"),
            "指標": "売上",
            "ブランド": sex,
            "SPU": age,
            "値": metrics["sales"],
            "金額": metrics["sales"],
            "数量": metrics["units"],
            "補足": f"orders={int(metrics['orders'])}",
        })
    for hour, metrics in sorted(hour_sales.items()):
        rows.append({
            **_base(config, f"profile_hour|{hour}", "購入時間帯 売上"),
            "指標": "売上",
            "月": hour,
            "値": metrics["sales"],
            "金額": metrics["sales"],
            "数量": metrics["units"],
            "補足": f"orders={int(metrics['orders'])}",
        })

    return rows


def _existing_by_key(config: StoreConfig, table_id: str) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    for item in _records(config, table_id):
        fields = item.get("fields") or {}
        key = _text(fields.get("キー"))
        if key:
            existing[key] = item
    return existing


def _same(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    for key, value in incoming.items():
        old = existing.get(key)
        if isinstance(value, (int, float)):
            if _number(old) != float(value):
                return False
        elif _text(old) != _text(value):
            return False
    return True


def _upsert(config: StoreConfig, table_id: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    existing = _existing_by_key(config, table_id)
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        old = existing.get(str(row["キー"]))
        if not old:
            creates.append(row)
        elif _same(old.get("fields") or {}, row):
            skipped += 1
        else:
            updates.append({"record_id": old["record_id"], "fields": row})

    base_path = f"/tables/{table_id}/records"
    for i in range(0, len(creates), 500):
        _post(config.app_token, f"{base_path}/batch_create", {"records": [{"fields": row} for row in creates[i:i + 500]]})
    for i in range(0, len(updates), 500):
        _post(config.app_token, f"{base_path}/batch_update", {"records": updates[i:i + 500]})
    return {"created": len(creates), "updated": len(updates), "skipped": skipped}


def _ensure_views(config: StoreConfig, table_id: str, chart_names: list[str]) -> list[str]:
    data = _get(config.app_token, f"/tables/{table_id}/views", page_size=100)
    existing = {view.get("view_name") for view in (data.get("data") or {}).get("items") or []}
    created: list[str] = []
    for index, chart in enumerate(chart_names, 1):
        name = f"{index:02d}_{chart}"
        if name in existing:
            continue
        _post(config.app_token, f"/tables/{table_id}/views", {"view_name": name, "view_type": "grid"})
        created.append(name)
    return created


def run_yearly_dashboard_charts(store_id: str = "default") -> dict[str, Any]:
    config = _store_config(store_id)
    table_id = _ensure_target_table(config)
    rows = _build_rows(config)
    result = _upsert(config, table_id, rows)
    charts = defaultdict(int)
    for row in rows:
        charts[str(row.get("グラフ"))] += 1
    created_views = _ensure_views(config, table_id, list(charts.keys()))
    return {
        "success": True,
        "store_id": config.store_id,
        "store_label": config.label,
        "target_table": TARGET_TABLE_NAME,
        "target_table_id": table_id,
        "rows": len(rows),
        "charts": dict(charts),
        "created_views": created_views,
        **result,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--store-id", default="default")
    args = parser.parse_args()
    print(json.dumps(run_yearly_dashboard_charts(args.store_id), ensure_ascii=False, indent=2))
