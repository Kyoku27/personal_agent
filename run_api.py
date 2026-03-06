"""
Web API 入口，供 agent-web 前端调用。
在 agent 目录下运行：python run_api.py
"""
import sys
from pathlib import Path

# 保证从 agent 目录运行时能正确导入 src
sys.path.insert(0, str(Path(__file__).resolve().parent))

import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Body
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


@app.post("/api/feishu/amazon-keyword/track")
def feishu_amazon_keyword_track(body: dict | None = Body(default=None)):
    """Amazon 关键词自然位/広告位追踪。可选 body: {"sheet": "KW追踪"}"""
    body = body or {}
    sheet_title = body.get("sheet")
    return run_keyword_tracking(sheet_title=sheet_title)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
