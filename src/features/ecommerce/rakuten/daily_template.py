from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

import requests

from src.core.config_manager import get_env
from src.features.feishu.bot_client import FEISHU_BASE_URL, _get_tenant_access_token
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable


RAKUTEN_DETAIL_TABLE = "楽天販売詳細"
SOURCE_SKU_FIELD = "システム連携用SKU番号"
SOURCE_ORDER_DATE_FIELD = "注文日"
SOURCE_QTY_FIELD = "個数"
SOURCE_AMOUNT_FIELD = "合計金額"
SOURCE_STATUS_CODE_FIELD = "参考code"


@dataclass(frozen=True)
class MonthSpec:
    year: int
    month: int

    @property
    def table_name(self) -> str:
        return f"{self.month}月_日別"

    @property
    def first_day(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    @property
    def previous_month_last_day(self) -> dt.date:
        return self.first_day - dt.timedelta(days=1)

    @property
    def next_month_first_day(self) -> dt.date:
        if self.month == 12:
            return dt.date(self.year + 1, 1, 1)
        return dt.date(self.year, self.month + 1, 1)


class LarkBaseClient:
    def __init__(self, base_token: str, token: str | None = None):
        self.base_token = base_token
        self.token = token or _get_tenant_access_token()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = requests.request(
            method,
            f"{FEISHU_BASE_URL}{path}",
            headers=self.headers,
            timeout=30,
            **kwargs,
        )
        if not resp.ok:
            body = resp.text[:1000]
            raise RuntimeError(
                f"Lark API HTTP {resp.status_code} {method} {path}: {body}"
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark API failed: {data}")
        return data.get("data") or {}

    def list_tables(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{self.base_token}/tables",
                params=params,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token") or ""
            if not page_token:
                return items

    def get_table_by_name(self, name: str) -> dict[str, Any] | None:
        for table in self.list_tables():
            if table.get("name") == name:
                return table
        return None

    def create_table(self, name: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{self.base_token}/tables",
            json={"table": {"name": name}},
        )
        return data.get("table") or data

    def list_fields_v1(self, table_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 200}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields",
                params=params,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token") or ""
            if not page_token:
                return items

    def update_field_v3(self, table_id: str, field_id: str, body: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "PUT",
            f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields/{field_id}",
            json=body,
        )
        return data.get("field") or data

    def create_field_v3(self, table_id: str, body: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields",
            json=body,
        )
        return data.get("field") or data

    def list_records(self, table_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records",
                params=params,
            )
            records.extend(data.get("items") or [])
            if not data.get("has_more"):
                return records
            page_token = data.get("page_token") or ""
            if not page_token:
                return records

    def batch_create_records(self, table_id: str, records: list[dict[str, Any]]) -> int:
        created = 0
        for idx in range(0, len(records), 500):
            chunk = records[idx : idx + 500]
            self._request(
                "POST",
                f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/batch_create",
                json={"records": chunk},
            )
            created += len(chunk)
        return created

    def batch_update_records(self, table_id: str, records: list[dict[str, Any]]) -> int:
        updated = 0
        for idx in range(0, len(records), 500):
            chunk = records[idx : idx + 500]
            self._request(
                "POST",
                f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/batch_update",
                json={"records": chunk},
            )
            updated += len(chunk)
        return updated


def parse_month(value: str | None, *, default_year: int | None = None) -> MonthSpec:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("month is required, use YYYY-MM or month number")
    year = default_year or dt.date.today().year
    if re.fullmatch(r"\d{4}-\d{1,2}", raw):
        y, m = raw.split("-", 1)
        year, month = int(y), int(m)
    elif re.fullmatch(r"\d{1,2}", raw):
        month = int(raw)
    elif re.fullmatch(r"\d{1,2}月", raw):
        month = int(raw[:-1])
    elif re.fullmatch(r"\d{1,2}月_日別", raw):
        month = int(raw.split("月", 1)[0])
    else:
        raise ValueError("month format must be YYYY-MM, 3, 3月, or 3月_日別")
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    return MonthSpec(year=year, month=month)


def _jst_month_start_ms(spec: MonthSpec) -> int:
    jst = dt.timezone(dt.timedelta(hours=9))
    return int(dt.datetime(spec.year, spec.month, 1, tzinfo=jst).timestamp() * 1000)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_cell_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "label"):
            if value.get(key) is not None:
                return _cell_text(value.get(key))
        return str(value)
    return str(value).strip()


def _copy_seed_records(
    client: LarkBaseClient,
    *,
    source_table_id: str,
    target_table_id: str,
    month: MonthSpec,
    dry_run: bool,
) -> int:
    """从源表复制所有数据行，将 対象月 设置为目标月份。"""
    source_records = client.list_records(source_table_id)
    target_fields = client.list_fields_v1(target_table_id)
    target_field_names = {f.get("field_name") for f in target_fields if f.get("field_name")}
    
    target_month = _jst_month_start_ms(month)
    records: list[dict[str, Any]] = []
    
    for record in source_records:
        source_fields = record.get("fields") or {}
        # 复制所有源字段到新记录（但只保留目标表中存在的字段）
        new_fields: dict[str, Any] = {}
        for field_name, value in source_fields.items():
            if field_name in target_field_names:
                new_fields[field_name] = value
        # 覆盖 対象月 为目标月份
        if "対象月" in target_field_names:
            new_fields["対象月"] = target_month
        
        # 只有当有有效字段时才添加记录
        if new_fields:
            records.append({"fields": new_fields})
    
    if dry_run:
        return len(records)
    
    if not records:
        return 0
    
    return client.batch_create_records(target_table_id, records)


def _set_target_month_for_records(
    client: LarkBaseClient,
    *,
    target_table_id: str,
    month: MonthSpec,
    dry_run: bool,
) -> int:
    records = client.list_records(target_table_id)
    target_month = _jst_month_start_ms(month)
    updates = [
        {"record_id": record["record_id"], "fields": {"対象月": target_month}}
        for record in records
        if record.get("record_id")
    ]
    if dry_run:
        return len(updates)
    return client.batch_update_records(target_table_id, updates)


def _get_user_access_token() -> str:
    return (
        get_env("LARK_USER_ACCESS_TOKEN")
        or get_env("FEISHU_USER_ACCESS_TOKEN")
        or ""
    ).strip()


def _lookup_sum_field(
    name: str,
    select: str,
    month: MonthSpec,
    *,
    exclude_cancelled: bool = False,
) -> dict[str, Any]:
    conditions: list[list[Any]] = [
        [SOURCE_SKU_FIELD, "==", {"type": "field_ref", "field": "Tag"}],
        [SOURCE_ORDER_DATE_FIELD, ">", {"type": "constant", "value": f"ExactDate({month.previous_month_last_day.isoformat()})"}],
        [SOURCE_ORDER_DATE_FIELD, "<", {"type": "constant", "value": f"ExactDate({month.next_month_first_day.isoformat()})"}],
    ]
    if exclude_cancelled:
        conditions.append([SOURCE_STATUS_CODE_FIELD, "!=", {"type": "constant", "value": 900}])
    return {
        "field_name": name,
        "type": 2005,
        "ui_type": "lookup",
        "property": {
            "lookup_table_name": RAKUTEN_DETAIL_TABLE,
            "lookup_field_name": select,
            "filter": {"logic": "and", "conditions": conditions},
            "result_type": "sum",
        },
    }


def _daily_lookup_field(day: int) -> dict[str, Any]:
    return {
        "field_name": f"{day}日",
        "type": 2005,
        "ui_type": "lookup",
        "property": {
            "lookup_table_name": RAKUTEN_DETAIL_TABLE,
            "lookup_field_name": SOURCE_AMOUNT_FIELD,
            "filter": {
                "logic": "and",
                "conditions": [
                    [SOURCE_SKU_FIELD, "==", {"type": "field_ref", "field": "Tag"}],
                    [SOURCE_ORDER_DATE_FIELD, "==", {"type": "field_ref", "field": f"{day}日付"}],
                ],
            },
            "result_type": "sum",
        },
    }


def _formula_date_field(day: int) -> dict[str, Any]:
    return {
        "field_name": f"{day}日付",
        "type": 2006,
        "ui_type": "formula",
        "property": {
            "expression": f"DATE(YEAR([対象月]),MONTH([対象月]),{day})",
        },
    }


def _build_template_fields(month: MonthSpec) -> list[dict[str, Any]]:
    days = calendar.monthrange(month.year, month.month)[1]
    fields: list[dict[str, Any]] = [
        {"field_name": "Tag", "type": 1, "ui_type": "text"},
        {"field_name": "Tag2HRP", "type": 1, "ui_type": "text"},
        {"field_name": "対象月", "type": 5, "ui_type": "date", "property": {"date_format": "yyyy/MM/dd"}},
    ]
    fields.extend(_formula_date_field(day) for day in range(1, days + 1))
    fields.extend(
        [
            _lookup_sum_field("合計", SOURCE_QTY_FIELD, month),
            _lookup_sum_field("金額", SOURCE_AMOUNT_FIELD, month),
        ]
    )
    fields.extend(_daily_lookup_field(day) for day in range(1, days + 1))
    fields.append(_lookup_sum_field("キャンセル以外_合計", SOURCE_QTY_FIELD, month, exclude_cancelled=True))
    return fields


def create_rakuten_daily_template(
    *,
    target_month: str,
    source_month: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = parse_month(target_month)
    source = parse_month(source_month, default_year=target.year) if source_month else MonthSpec(
        year=target.year if target.month > 1 else target.year - 1,
        month=target.month - 1 if target.month > 1 else 12,
    )

    wiki_node_token = get_env("FEISHU_RAKUTEN_WIKI_NODE_TOKEN") or "TLGPwyCYpiFGKVkqAXnji9iupue"
    base_token = resolve_wiki_to_bitable(wiki_node_token)
    user_token = _get_user_access_token()
    client = LarkBaseClient(base_token, token=user_token or None)
    auth_mode = "user" if user_token else "tenant"

    source_table = client.get_table_by_name(source.table_name)
    if not source_table:
        return {
            "success": False,
            "message": f"找不到参考表：{source.table_name}（当前使用 {auth_mode} token）",
            "auth_mode": auth_mode,
        }
    
    source_table_id = source_table.get("table_id") or source_table.get("id")
    
    # 检查目标表是否已存在
    existing = client.get_table_by_name(target.table_name)
    if existing:
        target_table_id = existing.get("table_id") or existing.get("id")
        updated = _set_target_month_for_records(
            client,
            target_table_id=target_table_id,
            month=target,
            dry_run=dry_run,
        )
        return {
            "success": True,
            "message": f"{target.table_name} 已存在；已更新 対象月 为 {target.year:04d}/{target.month:02d}/01（{updated} 行）",
            "table_id": target_table_id,
            "target_table_name": target.table_name,
            "records_updated": updated,
            "auth_mode": auth_mode,
        }

    # dry_run 模式
    if dry_run:
        source_fields = client.list_fields_v1(source_table_id)
        source_records = client.list_records(source_table_id)
        return {
            "success": True,
            "message": f"dry-run: 将从 {source.table_name} 克隆到 {target.table_name}",
            "source_table_id": source_table_id,
            "target_table_name": target.table_name,
            "fields_count": len(source_fields),
            "records_count": len(source_records),
            "auth_mode": auth_mode,
        }

    # 创建新表
    created = client.create_table(target.table_name)
    target_table_id = created.get("id") or created.get("table_id")
    if not target_table_id:
        raise RuntimeError(f"表创建后缺少 ID: {created}")

    # 从源表读取所有字段定义
    source_fields = client.list_fields_v1(source_table_id)
    
    # 获取目标表的默认字段
    target_default_fields = client.list_fields_v1(target_table_id)
    if not target_default_fields:
        raise RuntimeError(f"创建的表没有默认字段: {target_table_id}")
    
    default_field_id = target_default_fields[0]["field_id"]
    
    # 第一个非隐藏字段用于更新默认字段
    first_field = next((f for f in source_fields if not f.get("is_hidden")), None)
    if first_field:
        client.update_field_v3(
            target_table_id,
            default_field_id,
            {"type": first_field.get("type", 1), "field_name": first_field.get("field_name", "Name")},
        )
    
    # 复制所有其他字段（跳过第一个已更新的）
    for field in source_fields:
        if field.get("field_id") == default_field_id:
            # 这是默认字段，已经处理过了
            continue
        if field.get("is_hidden"):
            # 跳过隐藏字段
            continue
        
        try:
            client.create_field_v3(target_table_id, field)
        except Exception as e:
            print(f"  ⚠️ 字段复制失败 {field.get('field_name')}: {e}")

    # 从源表复制 SKU 数据行（保留所有字段）
    copied = _copy_seed_records(
        client,
        source_table_id=source_table_id,
        target_table_id=target_table_id,
        month=target,
        dry_run=False,
    )
    
    return {
        "success": True,
        "message": (
            f"已从 {source.table_name} 克隆到 {target.table_name}："
            f"字段 {len(source_fields)} 个，SKU 行 {copied} 条；"
            f"対象月已设置为 {target.year:04d}/{target.month:02d}/01"
        ),
        "source_table_id": source_table_id,
        "target_table_id": target_table_id,
        "target_table_name": target.table_name,
        "records_created": copied,
        "fields_count": len(source_fields),
        "auth_mode": auth_mode,
    }
