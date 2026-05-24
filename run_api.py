from __future__ import annotations

import csv
import json
import sys
import urllib.request
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_rakuten_orders import run_rakuten_orders_sync
from src.features.ecommerce.rakuten.daily_template import inspect_daily_tables, prepare_daily_template
from src.features.ecommerce.amazon.keyword_tracker import run_keyword_tracking
from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync
from src.features.feishu.user_oauth import build_oauth_url, exchange_code_and_store
from src.features.page_analysis import PageAnalyzer


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Agent Hub API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/lark/oauth/url")
def lark_oauth_url():
    try:
        return {"success": True, "url": build_oauth_url()}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@app.get("/api/lark/oauth/callback")
def lark_oauth_callback(code: str = ""):
    try:
        return exchange_code_and_store(code)
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@app.post("/api/page-analysis/analyze")
def analyze_page(body: dict = Body(...)):
    url = body.get("url", "")
    if not url:
        return {"error": "url is required"}
    result = PageAnalyzer().analyze(url)
    return {
        "url": result.url,
        "title": result.title,
        "meta_description": result.meta_description,
        "h1_list": result.h1_list,
        "og_title": result.og_title,
        "og_description": result.og_description,
        "error": result.error,
    }


@app.post("/api/ecommerce/rakuten/sync")
def rakuten_sync(body: dict | None = Body(default=None)):
    body = body or {}
    try:
        return run_rakuten_orders_sync(
            date_str=body.get("date"),
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
            dry_run=bool(body.get("dry_run")),
            inspect=bool(body.get("inspect")),
            store_id=body.get("store_id") or "default",
        )
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@app.post("/api/feishu/sync")
def feishu_sync():
    return rakuten_sync({})


@app.post("/api/ecommerce/rakuten/daily-template")
def rakuten_daily_template(body: dict | None = Body(default=None)):
    body = body or {}
    try:
        return prepare_daily_template(
            source_month=body.get("source_month") or "",
            target_month=body.get("target_month") or "",
            dry_run=bool(body.get("dry_run")),
            store_id=body.get("store_id") or "default",
        )
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@app.get("/api/ecommerce/rakuten/daily-template/tables")
def rakuten_daily_template_tables(store_id: str = "default"):
    try:
        return inspect_daily_tables(store_id=store_id)
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@app.post("/api/feishu/amazon-rank/sync")
def feishu_amazon_rank_sync(body: dict | None = Body(default=None)):
    body = body or {}
    return run_amazon_rank_sync(sheet_title=body.get("sheet"))


@app.post("/api/feishu/amazon-rank/sync/stream")
async def feishu_amazon_rank_sync_stream(request: Request, body: dict | None = Body(default=None)):
    body = body or {}

    async def event_generator():
        from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync_generator

        for item in run_amazon_rank_sync_generator(sheet_title=body.get("sheet")):
            if await request.is_disconnected():
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.post("/api/feishu/amazon-keyword/track")
def feishu_amazon_keyword_track(body: dict | None = Body(default=None)):
    body = body or {}
    return run_keyword_tracking(sheet_title=body.get("sheet"))


@app.post("/api/feishu/amazon-keyword/track/stream")
async def feishu_amazon_keyword_track_stream(request: Request, body: dict | None = Body(default=None)):
    body = body or {}

    async def event_generator():
        yield json.dumps({"type": "progress", "message": "Starting keyword tracking..."}, ensure_ascii=False) + "\n"
        try:
            result = run_keyword_tracking(sheet_title=body.get("sheet"))
            yield json.dumps({"type": "done", "message": result.get("message", "Keyword tracking finished")}, ensure_ascii=False) + "\n"
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.get("/api/inventory/dashboard")
def get_inventory_dashboard(sheet: str = "rakuten"):
    sheet_gid_map = {"rakuten": "224017440", "all": "854399422"}
    gid = sheet_gid_map.get(sheet, "224017440")
    base_url = "https://docs.google.com/spreadsheets/d/12LFt2HVxZpAb9WKlcEksx5D35eJjaW6lZ8Ld4EN_2Ak"
    sheet_csv_url = f"{base_url}/export?format=csv&gid={gid}"
    inventory: list[dict[str, str]] = []
    try:
        req = urllib.request.Request(sheet_csv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            rows = list(csv.reader(StringIO(response.read().decode("utf-8"))))
        last_brand = ""
        for row in rows[3:]:
            if len(row) < 2:
                continue
            a_val = row[0].strip() if row[0] else ""
            b_val = row[1].strip() if len(row) > 1 and row[1] else ""
            c_val = row[2].strip() if len(row) > 2 and row[2] else ""
            e_val = row[4].strip() if len(row) > 4 and row[4] else ""
            if a_val and not b_val and not c_val and not e_val:
                last_brand = a_val
                continue
            if not b_val or (not e_val and not c_val):
                continue
            inventory.append({
                "brand": last_brand or "Uncategorized",
                "sku": e_val,
                "name": c_val,
                "stock": row[7].strip() if len(row) > 7 and row[7] else "0",
            })
    except Exception as exc:
        return {"success": False, "message": f"Fetch Google Sheet failed: {exc}"}
    return {
        "success": True,
        "sales": {
            "rakuten": {"today_orders": None, "total_orders": None, "revenue": None},
            "yahoo": {"today_orders": None, "total_orders": None, "revenue": None},
            "shopify": {"today_orders": None, "total_orders": None, "revenue": None},
        },
        "inventory": inventory,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
