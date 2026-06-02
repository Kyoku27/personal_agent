from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from run_dashboard_sheet_export import run_dashboard_sheet_export
from run_rakuten_orders import run_rakuten_orders_sync
from run_sku_review import inspect_pending_skus
from run_tomtoc_dashboard_sheet import run_tomtoc_dashboard_sheet
from src.features.ecommerce.rakuten.weekly_sheet import run_tomtoc_weekly_sheet_sync

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_STORES = ("default", "store2")
STATE_FILE = Path(__file__).resolve().parent / "logs" / "daily_ecommerce_update_state.json"


def _default_target_date() -> str:
    return (datetime.now(JST).date() - timedelta(days=1)).isoformat()


def _parse_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_date_range(target_date: str | None = None, start_date: str | None = None, end_date: str | None = None) -> tuple[str, str, str]:
    yesterday = _parse_date(_default_target_date(), "default target date")
    if target_date:
        target = _parse_date(target_date, "date")
        return target.isoformat(), target.isoformat(), "manual-date"
    if start_date or end_date:
        if not start_date or not end_date:
            raise ValueError("start_date and end_date must be provided together")
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
        if start > end:
            raise ValueError("start_date cannot be after end_date")
        return start.isoformat(), end.isoformat(), "manual-range"

    state = _load_state()
    last_success = state.get("last_successful_date")
    if last_success:
        start = _parse_date(str(last_success), "last_successful_date") + timedelta(days=1)
    else:
        start = yesterday
    if start > yesterday:
        start = yesterday
    return start.isoformat(), yesterday.isoformat(), "catch-up"


def _run_step(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    started_at = datetime.now(JST).isoformat(timespec="seconds")
    print(f"[{started_at}] START {name}", flush=True)
    try:
        result = func()
        success = bool(result.get("success", True))
        finished_at = datetime.now(JST).isoformat(timespec="seconds")
        print(f"[{finished_at}] {'OK' if success else 'NG'} {name}", flush=True)
        return {
            "name": name,
            "success": success,
            "started_at": started_at,
            "finished_at": finished_at,
            "result": result,
        }
    except Exception as exc:
        finished_at = datetime.now(JST).isoformat(timespec="seconds")
        print(f"[{finished_at}] ERROR {name}: {exc}", flush=True)
        return {
            "name": name,
            "success": False,
            "started_at": started_at,
            "finished_at": finished_at,
            "error": str(exc),
        }


def _write_log(payload: dict[str, Any]) -> Path:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"daily_ecommerce_update_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_daily_ecommerce_update(
    target_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    stores: tuple[str, ...] = DEFAULT_STORES,
    skip_orders: bool = False,
    skip_weekly: bool = False,
    skip_dashboards: bool = False,
    dry_run_orders: bool = False,
    dry_run_weekly: bool = False,
) -> dict[str, Any]:
    start_str, end_str, run_mode = _resolve_date_range(target_date=target_date, start_date=start_date, end_date=end_date)
    steps: list[dict[str, Any]] = []

    if not skip_orders:
        for store_id in stores:
            steps.append(
                _run_step(
                    f"rakuten_orders:{store_id}",
                    lambda store_id=store_id: run_rakuten_orders_sync(
                        start_date=start_str,
                        end_date=end_str,
                        dry_run=dry_run_orders,
                        store_id=store_id,
                    ),
                )
            )

    if not skip_weekly:
        steps.append(
            _run_step(
                "tomtoc_weekly_sheet",
                lambda: run_tomtoc_weekly_sheet_sync(
                    start_date=start_str,
                    end_date=end_str,
                    dry_run=dry_run_weekly,
                ),
            )
        )

    if not skip_orders:
        steps.append(
            _run_step(
                "sku_review",
                lambda: inspect_pending_skus(
                    store_ids=stores,
                    start_date=start_str,
                    end_date=end_str,
                    write_status=True,
                ),
            )
        )

    if not skip_dashboards:
        steps.append(
            _run_step(
                "ezlife_dashboard",
                lambda: run_dashboard_sheet_export(store_id="default", include_base_link=False),
            )
        )
        steps.append(_run_step("tomtoc_dashboard", run_tomtoc_dashboard_sheet))

    success = all(step["success"] for step in steps)
    payload = {
        "success": success,
        "target_date": end_str,
        "start_date": start_str,
        "end_date": end_str,
        "run_mode": run_mode,
        "started_by": "run_daily_ecommerce_update.py",
        "finished_at": datetime.now(JST).isoformat(timespec="seconds"),
        "steps": steps,
    }
    payload["log_path"] = str(_write_log(payload))
    if success and not dry_run_orders and not dry_run_weekly and not skip_orders and not skip_weekly and not skip_dashboards:
        _write_state(
            {
                "last_successful_date": end_str,
                "updated_at": payload["finished_at"],
                "log_path": payload["log_path"],
            }
        )
    print(f"[{payload['finished_at']}] LOG {payload['log_path']}", flush=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily EC data sync and dashboard updates.")
    parser.add_argument("--date", help="Target order date in YYYY-MM-DD. Defaults to yesterday in JST.")
    parser.add_argument("--start-date", help="Start date in YYYY-MM-DD for manual catch-up.")
    parser.add_argument("--end-date", help="End date in YYYY-MM-DD for manual catch-up.")
    parser.add_argument("--stores", nargs="+", default=list(DEFAULT_STORES), help="Rakuten stores to sync.")
    parser.add_argument("--skip-orders", action="store_true")
    parser.add_argument("--skip-weekly", action="store_true")
    parser.add_argument("--skip-dashboards", action="store_true")
    parser.add_argument("--dry-run-orders", action="store_true")
    parser.add_argument("--dry-run-weekly", action="store_true")
    args = parser.parse_args()

    result = run_daily_ecommerce_update(
        target_date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        stores=tuple(args.stores),
        skip_orders=args.skip_orders,
        skip_weekly=args.skip_weekly,
        skip_dashboards=args.skip_dashboards,
        dry_run_orders=args.dry_run_orders,
        dry_run_weekly=args.dry_run_weekly,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
