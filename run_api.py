"""
Web API 入口，供 agent-web 前端调用。
在 agent 目录下运行：python run_api.py
"""
import sys
from pathlib import Path

# 保证从 agent 目录运行时能正确导入 src
sys.path.insert(0, str(Path(__file__).resolve().parent))

import datetime
import csv
import urllib.request
import json
from io import StringIO
from contextlib import asynccontextmanager

from fastapi import FastAPI, Body, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from src.features.page_analysis import PageAnalyzer
from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.ecommerce.rakuten.data_analyzer import RakutenDataAnalyzer
from src.features.feishu.bot_client import FeishuBotClient
from src.features.feishu.sheet_manager import FeishuSheetManager
from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync
from src.features.ecommerce.amazon.keyword_tracker import run_keyword_tracking


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # 可在此做清理


app = FastAPI(title="个人智能体 API", lifespan=lifespan)

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


@app.post("/api/page-analysis/analyze")
def analyze_page(body: dict = Body(...)):
    url = body.get("url", "")
    if not url:
        return {"error": "缺少 url 参数"}
    analyzer = PageAnalyzer()
    result = analyzer.analyze(url)
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
    date_str = body.get("date")
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "message": "日期格式错误，请使用 YYYY-MM-DD"}
    else:
        target_date = datetime.date.today() - datetime.timedelta(days=1)

    try:
        rakuten_client = RakutenApiClient()
        analyzer = RakutenDataAnalyzer(client=rakuten_client)
        summary_data_list = analyzer.get_revenue_summary(target_date)

        feishu_client = FeishuBotClient(bot_token=get_env("FEISHU_BOT_TOKEN", "") or "dummy")
        sheet_manager = FeishuSheetManager(client=feishu_client)
        success_count = 0
        for sku_data in summary_data_list:
            sheet_manager.upsert_pivot_revenue_record(
                app_token=None,
                table_id=None,
                target_date=target_date,
                data=sku_data,
            )
            success_count += 1
        return {"success": True, "message": f"已同步 {success_count} 条记录"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/feishu/sync")
def feishu_sync():
    """手动触发飞书同步（复用乐天同步逻辑，默认昨日）"""
    return rakuten_sync({})


@app.post("/api/feishu/amazon-rank/sync")
def feishu_amazon_rank_sync(body: dict | None = Body(default=None)):
    """Amazon 排名同步到飞书电子表格。可选 body: {"sheet": "3月"}"""
    body = body or {}
    sheet_title = body.get("sheet")
    return run_amazon_rank_sync(sheet_title=sheet_title)


@app.post("/api/feishu/amazon-rank/sync/stream")
async def feishu_amazon_rank_sync_stream(request: Request, body: dict | None = Body(default=None)):
    """流式返回的 Amazon 排名同步。"""
    body = body or {}
    sheet_title = body.get("sheet")
    
    async def event_generator():
        from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync_generator
        gen = run_amazon_rank_sync_generator(sheet_title=sheet_title)
        
        for item in gen:
            # 每次拿到一个事件时，检查客户端是否已断开（取消请求）
            if await request.is_disconnected():
                print("Client disconnected, cancelling task!")
                break
                
            yield json.dumps(item) + "\n"
            
    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.post("/api/feishu/amazon-keyword/track")
def feishu_amazon_keyword_track(body: dict | None = Body(default=None)):
    """Amazon 关键词自然位/広告位追踪。可选 body: {"sheet": "KW追踪"}"""
    body = body or {}
    sheet_title = body.get("sheet")
    return run_keyword_tracking(sheet_title=sheet_title)


@app.get("/api/inventory/dashboard")
def get_inventory_dashboard(sheet: str = "rakuten"):
    """
    读取 Google Sheets 社内库存数据。
    楽天・ヤフ tab (GID=224017440):
      - A列(0) = 商品群(品牌) — 合并单元格, 需要 forward-fill
      - B列(1) = 商品ID
      - C列(2) = 商品名
      - E列(4) = 型番 / SKU
      - H列(7) = 当社在庫(社内库存)
      - 真实数据从第 8 行开始 (index 7)
    """
    # 各平台销售数据（未接连真实 API）
    sales_data = {
        "rakuten": {"today_orders": None, "total_orders": None, "revenue": None},
        "yahoo": {"today_orders": None, "total_orders": None, "revenue": None},
        "shopify": {"today_orders": None, "total_orders": None, "revenue": None},
    }

    # 确认的 sheet GID:
    # - 楽天・ヤフ (Rakuten + Yahoo): GID = 224017440
    # - 全得意計 (Total summary) : GID = 854399422
    sheet_gid_map = {
        "rakuten": "224017440",
        "all": "854399422",
    }
    gid = sheet_gid_map.get(sheet, "224017440")
    base_url = "https://docs.google.com/spreadsheets/d/12LFt2HVxZpAb9WKlcEksx5D35eJjaW6lZ8Ld4EN_2Ak"
    sheet_csv_url = f"{base_url}/export?format=csv&gid={gid}"

    google_sheet_inventory = []
    try:
        req = urllib.request.Request(sheet_csv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode("utf-8")
            reader = csv.reader(StringIO(content))
            all_rows = list(reader)

            # 真实表格结构（从第4行开始读取，跳过前3行标题）:
            # - 品牌标题行: A列=品牌名, B列=空 (如 "CZUR", "homerunPET")
            # - 数据行: A列=空, B列=商品ID, C列=商品名, E列=型番/SKU, H列=当社在庫
            # 跳过前3行（行1:空，行2:楽天链接，行3:スーパーセール等）
            # 从第4行开始（index=3）是列标题行
            data_rows = all_rows[3:]
            print(f"[inventory] Total rows in sheet: {len(all_rows)}, scanning from row 4")

            last_brand = ""
            for row in data_rows:
                # 至少要有2列
                if len(row) < 2:
                    continue

                a_val = row[0].strip() if row[0] else ""
                b_val = row[1].strip() if len(row) > 1 and row[1] else ""
                c_val = row[2].strip() if len(row) > 2 and row[2] else ""
                e_val = row[4].strip() if len(row) > 4 and row[4] else ""

                # 排除标题行 (如果 B列是 "商品ID" 或 E列是 "型番")
                if b_val == "商品ID" or e_val == "型番":
                    continue

                # 品牌标题行判定: A列有值 且 (B, C, E列均为空)
                # 这种行通常是隔开不同品牌的标题行
                if a_val and not b_val and not c_val and not e_val:
                    if a_val not in ["商品群", "当社", "在庫"]:
                        last_brand = a_val
                    continue

                # 数据行判定: 必须有商品ID (B列)
                if not b_val:
                    continue

                # H 列 (index 7) = 社内库存
                stock = row[7].strip() if len(row) > 7 and row[7] else ""

                # 跳过完全空的数据内容
                if not e_val and not c_val:
                    continue

                google_sheet_inventory.append({
                    "brand": last_brand or "未分类",
                    "sku": e_val,
                    "name": c_val,
                    "stock": stock or "0"
                })

        print(f"[inventory] Loaded {len(google_sheet_inventory)} items")
    except Exception as e:
        print("Fetch Google Sheet failed:", e)
        import traceback; traceback.print_exc()
        return {
            "success": False,
            "message": f"无法读取 Google Sheets: {str(e)}"
        }

    return {
        "success": True,
        "sales": sales_data,
        "inventory": google_sheet_inventory
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
