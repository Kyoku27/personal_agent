"""
Rakuten 受注（前一天）同步到 Lark Wiki Bitable 的销售详细表。

用法：
  python run_rakuten_orders.py --inspect
  python run_rakuten_orders.py --dry-run
  python run_rakuten_orders.py
  python run_rakuten_orders.py --date 2026-04-30
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.ecommerce.rakuten.order_sync import decide_granularity, build_records, build_sync_warnings
from src.features.feishu.bot_client import FeishuBotClient
from src.features.feishu.sheet_manager import FeishuSheetManager
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable


def _cell_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_cell_values(item))
        return out
    if isinstance(value, dict):
        out = []
        for key in ("text", "name", "value", "label"):
            if value.get(key):
                out.extend(_cell_values(value.get(key)))
        return out or [str(value).strip()]
    text = str(value).strip()
    return [text] if text else []


def _load_spu_tag_values(sheet_manager: FeishuSheetManager) -> tuple[set[str], str | None]:
    table_id = get_env("FEISHU_SPU_TABLE_ID") or ""
    app_token = get_env("FEISHU_SPU_BITABLE_APP_TOKEN") or ""
    wiki_node_token = get_env("FEISHU_SPU_WIKI_NODE_TOKEN") or ""
    tag_field = get_env("FEISHU_SPU_TAG_FIELD") or "tag_spu"
    if not table_id or not (app_token or wiki_node_token):
        return set(), "未配置 SPU月别_DB，无法检查 tag_spu"
    if not app_token:
        app_token = resolve_wiki_to_bitable(wiki_node_token)

    records = sheet_manager.list_bitable_records(app_token=app_token, table_id=table_id)
    tags: set[str] = set()
    for record in records:
        fields = record.get("fields") or {}
        for text in _cell_values(fields.get(tag_field)):
            if text:
                tags.add(text)
    return tags, None


def _yesterday_jst() -> dt.date:
    # 简化：按本机日期计算 yesterday（运行环境为日本时区机器时即可）
    return dt.date.today() - dt.timedelta(days=1)


def run_rakuten_orders_sync(
    *,
    date_str: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    inspect: bool = False,
) -> dict[str, Any]:
    """
    供 CLI 与 API 复用的主逻辑。
    """
    try:
        # env
        wiki_node_token = get_env("FEISHU_RAKUTEN_WIKI_NODE_TOKEN") or ""
        table_id = get_env("FEISHU_RAKUTEN_ORDER_TABLE_ID") or ""
        if not wiki_node_token or not table_id:
            return {
                "success": False,
                "message": "缺少环境变量：FEISHU_RAKUTEN_WIKI_NODE_TOKEN / FEISHU_RAKUTEN_ORDER_TABLE_ID",
            }

        # resolve wiki -> bitable app_token
        app_token = resolve_wiki_to_bitable(wiki_node_token)

        # manager
        feishu_client = FeishuBotClient(bot_token=get_env("FEISHU_BOT_TOKEN") or "")
        sheet_manager = FeishuSheetManager(client=feishu_client)

        # inspect columns
        columns = sheet_manager.list_table_fields(app_token=app_token, table_id=table_id)
        if inspect:
            return {
                "success": True,
                "message": f"已读取列结构，共 {len(columns)} 列",
                "columns": columns,
                "table_id": table_id,
            }
        if not columns:
            return {
                "success": False,
                "message": "目标表没有可读列（可能是空表且无数据行），请先手动加一行示例数据。",
            }

        # date range (preferred) or single date
        if start_date or end_date:
            if not start_date or not end_date:
                return {"success": False, "message": "区间同步需要同时提供 start_date 和 end_date"}
            try:
                _ = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
                _ = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                return {"success": False, "message": "start_date/end_date 格式错误，请使用 YYYY-MM-DD"}
            range_start = start_date
            range_end = end_date
        else:
            if date_str:
                try:
                    target_date = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    return {"success": False, "message": "--date 格式错误，请使用 YYYY-MM-DD"}
            else:
                target_date = _yesterday_jst()
            range_start = target_date.strftime("%Y-%m-%d")
            range_end = range_start

        # fetch
        rakuten = RakutenApiClient()
        orders = rakuten.get_orders_detailed(start_date=range_start, end_date=range_end)

        # map
        granularity = decide_granularity(columns)
        pairs = build_records(orders, columns, granularity)
        spu_tags, spu_warning = _load_spu_tag_values(sheet_manager)

        if dry_run:
            plan = sheet_manager.bitable_bulk_upsert_records(
                app_token,
                table_id,
                records=pairs,
                dry_run=True,
            )
            warnings = build_sync_warnings(pairs, columns, plan, spu_tags=spu_tags, spu_warning=spu_warning)
            preview = []
            for fields, key_fields in pairs[:5]:
                preview.append({"keys": key_fields, "fields": fields})
            return {
                "success": True,
                "message": (
                    f"dry-run 完成：受注 {len(orders)} 单，读取现有 {plan['existing_records_scanned']} 行；"
                    f"计划新增 {plan['planned_creates']} 行、更新 {plan['planned_updates']} 行、跳过 {plan['skipped']} 行"
                ),
                "start_date": range_start,
                "end_date": range_end,
                "granularity": granularity,
                "orders_count": len(orders),
                "rows_count": len(pairs),
                "planned_creates": plan["planned_creates"],
                "planned_updates": plan["planned_updates"],
                "skipped": plan["skipped"],
                "existing_records_scanned": plan["existing_records_scanned"],
                "warnings": warnings,
                "columns": columns,
                "preview": preview,
            }

        write_result = sheet_manager.bitable_bulk_upsert_records(
            app_token,
            table_id,
            records=pairs,
            dry_run=False,
        )
        warnings = build_sync_warnings(pairs, columns, write_result, spu_tags=spu_tags, spu_warning=spu_warning)

        return {
            "success": True,
            "message": (
                f"同步完成：新增 {write_result['created']} 行、更新 {write_result['updated']} 行、"
                f"跳过 {write_result['skipped']} 行（{range_start} ~ {range_end}）"
            ),
            "start_date": range_start,
            "end_date": range_end,
            "granularity": granularity,
            "orders_count": len(orders),
            "rows_count": len(pairs),
            "created": write_result["created"],
            "updated": write_result["updated"],
            "skipped": write_result["skipped"],
            "planned_creates": write_result["planned_creates"],
            "planned_updates": write_result["planned_updates"],
            "existing_records_scanned": write_result["existing_records_scanned"],
            "warnings": warnings,
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Rakuten orders to Lark Wiki Bitable")
    parser.add_argument("--date", type=str, help="Date in YYYY-MM-DD (defaults to yesterday)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch & map only, do not write")
    parser.add_argument("--inspect", action="store_true", help="Inspect target table columns only")
    args = parser.parse_args()

    try:
        result = run_rakuten_orders_sync(
            date_str=args.date,
            dry_run=args.dry_run,
            inspect=args.inspect,
        )
        if result.get("success"):
            print("✅", result.get("message"))
            if args.dry_run:
                print("\n--- dry-run preview (first 5 rows) ---")
                for i, item in enumerate(result.get("preview", []), start=1):
                    print(f"[{i}] keys={item.get('keys')} fields={item.get('fields')}")
            return

        print("❌", result.get("message"))
        sys.exit(1)
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
