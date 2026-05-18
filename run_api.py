"""
Web API 入口，供 agent-web 前端调用。
在 agent 目录下运行：python run_api.py
"""
import sys
from pathlib import Path

# 保证从 agent 目录运行时能正确导入 src
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csv
import urllib.request
import json
import datetime
from io import StringIO
from contextlib import asynccontextmanager

from fastapi import FastAPI, Body, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from src.features.page_analysis import PageAnalyzer
from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.ecommerce.rakuten.daily_template import create_rakuten_daily_template
from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync
from run_rakuten_orders import run_rakuten_orders_sync
from src.features.ecommerce.amazon.keyword_tracker import (
    run_keyword_tracking,
    add_keyword_to_master,
    get_keyword_history,
    load_keywords_from_lark
)


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
    """
    乐天受注同步（新流程，写 Wiki 下销售详细 Bitable）。
    可选 body:
      - {"date": "YYYY-MM-DD"}
      - {"dry_run": true}
      - {"inspect": true}
    """
    body = body or {}
    return run_rakuten_orders_sync(
        date_str=body.get("date"),
        start_date=body.get("start_date"),
        end_date=body.get("end_date"),
        dry_run=bool(body.get("dry_run", False)),
        inspect=bool(body.get("inspect", False)),
    )


@app.post("/api/ecommerce/rakuten/daily-template")
def rakuten_daily_template(body: dict | None = Body(default=None)):
    body = body or {}
    try:
        return create_rakuten_daily_template(
            target_month=str(body.get("target_month") or "").strip(),
            source_month=str(body.get("source_month") or "").strip() or None,
            dry_run=bool(body.get("dry_run", False)),
        )
    except Exception as e:
        return {"success": False, "message": str(e)}


def _normalize_order_number(value) -> str:
    return str(value or "").strip()


def _extract_basket_ids(order: dict) -> list[int]:
    basket_ids: list[int] = []
    for package in order.get("PackageModelList", []) or []:
        basket_id = package.get("basketId")
        try:
            if basket_id is not None:
                basket_ids.append(int(basket_id))
        except Exception:
            continue
    return basket_ids


def _default_delivery_company(order: dict, fallback: str) -> str:
    for package in order.get("PackageModelList", []) or []:
        code = package.get("defaultDeliveryCompanyCode")
        if code:
            return str(code)
    return fallback


@app.post("/api/ecommerce/rakuten/shipping/import")
def rakuten_shipping_import(body: dict = Body(...)):
    """
    Import tracking numbers from CSV-mapped rows and optionally report shipping completion to Rakuten.

    body:
      {
        "dry_run": true,
        "shipping_date": "YYYY-MM-DD",
        "default_delivery_company": "1003",
        "shipments": [
          {"order_number": "...", "shipping_number": "...", "delivery_company": "1003"}
        ]
      }
    """
    shipments = body.get("shipments") or []
    dry_run = bool(body.get("dry_run", True))
    shipping_date = str(body.get("shipping_date") or datetime.date.today().isoformat()).strip()
    default_delivery_company = str(body.get("default_delivery_company") or "1003").strip()

    if not isinstance(shipments, list) or not shipments:
        return {"success": False, "message": "没有可导入的发货单号", "results": []}

    normalized: list[dict] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for index, row in enumerate(shipments, start=1):
        order_number = _normalize_order_number(row.get("order_number"))
        shipping_number = str(row.get("shipping_number") or "").strip()
        delivery_company = str(row.get("delivery_company") or default_delivery_company).strip()
        if not order_number or not shipping_number:
            continue
        if order_number in seen:
            duplicates.append(order_number)
        seen.add(order_number)
        normalized.append({
            "row": index,
            "order_number": order_number,
            "shipping_number": shipping_number,
            "delivery_company": delivery_company,
        })

    if not normalized:
        return {"success": False, "message": "CSV 中没有同时包含订单号和发货单号的行", "results": []}

    client = RakutenApiClient()
    if not client.ping():
        return {"success": False, "message": "RAKUTEN_API_KEY / RAKUTEN_SHOP_ID 未配置", "results": []}

    orders = client.get_orders_by_numbers([row["order_number"] for row in normalized])
    order_by_number = {_normalize_order_number(order.get("orderNumber")): order for order in orders}

    results: list[dict] = []
    for row in normalized:
        order = order_by_number.get(row["order_number"])
        if not order:
            results.append({**row, "status": "not_found", "message": "楽天 API 没有返回该订单"})
            continue

        basket_ids = _extract_basket_ids(order)
        if not basket_ids:
            results.append({**row, "status": "missing_basket", "message": "订单缺少送付先ID，无法写入発送情報"})
            continue

        delivery_company = row["delivery_company"] or _default_delivery_company(order, default_delivery_company)
        basket_payload = [
            {
                "basketId": basket_id,
                "ShippingModelList": [
                    {
                        "shippingNumber": row["shipping_number"],
                        "deliveryCompany": delivery_company,
                        "shippingDate": shipping_date,
                    }
                ],
            }
            for basket_id in basket_ids
        ]

        if dry_run:
            results.append({
                **row,
                "status": "ready",
                "message": "可写入",
                "basket_ids": basket_ids,
                "delivery_company": delivery_company,
                "shipping_date": shipping_date,
                "multiple_baskets": len(basket_ids) > 1,
            })
            continue

        try:
            response = client.update_order_shipping(row["order_number"], basket_payload)
            messages = response.get("MessageModelList") or []
            has_error = any(str(msg.get("messageType") or "").upper() == "ERROR" for msg in messages)
            results.append({
                **row,
                "status": "error" if has_error else "updated",
                "message": "楽天已返回处理结果",
                "basket_ids": basket_ids,
                "delivery_company": delivery_company,
                "shipping_date": shipping_date,
                "rakuten_messages": messages,
            })
        except Exception as e:
            results.append({**row, "status": "error", "message": str(e), "basket_ids": basket_ids})

    updated = sum(1 for r in results if r["status"] == "updated")
    ready = sum(1 for r in results if r["status"] == "ready")
    errors = sum(1 for r in results if r["status"] in {"error", "not_found", "missing_basket"})
    return {
        "success": errors == 0,
        "dry_run": dry_run,
        "message": (
            f"Dry-run 完成：{ready} 件可写入，{errors} 件需处理"
            if dry_run
            else f"写入完成：{updated} 件已提交，{errors} 件失败"
        ),
        "summary": {
            "total": len(normalized),
            "ready": ready,
            "updated": updated,
            "errors": errors,
            "duplicates": sorted(set(duplicates)),
        },
        "results": results,
    }


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


@app.post("/api/feishu/amazon-keyword/track/stream")
async def feishu_amazon_keyword_track_stream(request: Request, body: dict | None = Body(default=None)):
    """流式返回的 Amazon 关键词追踪。"""
    body = body or {}
    sheet_title = body.get("sheet")
    
    async def event_generator():
        # 这里需要 keyword_tracker 提供一个 generator 版本
        from src.features.ecommerce.amazon.keyword_tracker import run_keyword_tracking_generator
        gen = run_keyword_tracking_generator(sheet_title=sheet_title)
        
        for item in gen:
            if await request.is_disconnected():
                print("Client disconnected, but keyword tracking continues in background...")
                # 注意：keyword tracking 涉及到抓取，如果断开连接建议让它继续跑完以防飞书数据不一致
                # 这里我们继续 consume 完但不再 yield
                continue
                
            yield json.dumps(item) + "\n"
            
    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.post("/api/ecommerce/amazon-keyword/add")
def api_amazon_keyword_add(body: dict = Body(...)):
    """向 Master 表添加新关键词"""
    brand = body.get("brand", "")
    asin = body.get("asin", "")
    product = body.get("product", "")
    keyword = body.get("keyword", "")
    if not asin or not keyword:
        return {"success": False, "message": "缺少 ASIN 或关键词"}
    return add_keyword_to_master(asin, keyword, brand=brand, product=product)
    
@app.get("/api/ecommerce/amazon-keyword/brands")
def api_amazon_keyword_brands():
    """获取 Master 表中所有已存在的品牌列表"""
    keywords = load_keywords_from_lark()
    brands = sorted(list(set([k.get("brand") for k in keywords if k.get("brand")])))
    return {"success": True, "results": brands}


@app.get("/api/ecommerce/amazon-keyword/list")
def api_amazon_keyword_list(asin: str):
    """根据 ASIN 获取已关联的关键词列表"""
    keywords = load_keywords_from_lark()
    # 过滤出匹配该 ASIN 的关键词
    results = [k for k in keywords if k.get("asin") == asin]
    return {"success": True, "results": results}


@app.get("/api/ecommerce/amazon-keyword/history")
def api_amazon_keyword_history(asin: str, keyword: str, limit: int = 50):
    """获取关键词的历史排名数据，用于绘制线状图"""
    history = get_keyword_history(asin, keyword, limit=limit)
    return {"success": True, "results": history}


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
    # 各平台销售数据
    sales_data = {
        "rakuten": {"today_orders": None, "total_orders": None, "revenue": None},
        "yahoo": {"today_orders": None, "total_orders": None, "revenue": None},
        "shopify": {"today_orders": None, "total_orders": None, "revenue": None},
    }

    # Rakuten 实时汇总（今天 / 本月累计）
    try:
        rakuten_client = RakutenApiClient()
        if rakuten_client.ping():
            today = datetime.date.today()
            today_str = today.strftime("%Y-%m-%d")
            month_start = today.replace(day=1).strftime("%Y-%m-%d")

            today_rows = rakuten_client.get_sales_data(today_str, today_str)
            month_rows = rakuten_client.get_sales_data(month_start, today_str)

            today_orders = int(sum(int(x.get("order_count", 0) or 0) for x in today_rows))
            today_revenue = float(sum(float(x.get("sales", 0) or 0) for x in today_rows))
            month_orders = int(sum(int(x.get("order_count", 0) or 0) for x in month_rows))

            sales_data["rakuten"] = {
                "today_orders": today_orders,
                "total_orders": month_orders,
                "revenue": today_revenue,
            }
    except Exception as e:
        # 不中断库存页面，仅保留未接入显示
        print(f"[inventory] Rakuten sales snapshot failed: {e}")

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


@app.get("/api/ecommerce/rakuten/capabilities")
def rakuten_capabilities():
    """
    返回当前系统可读取的 Rakuten 数据能力，方便前端/调试查看。
    """
    return {
        "success": True,
        "apis": {
            "order_search": {"endpoint": "/es/2.0/order/searchOrder/", "status": "implemented"},
            "order_detail": {"endpoint": "/es/2.0/order/getOrder/", "status": "implemented"},
            "sales_summary_by_sku": {"status": "implemented", "source": "searchOrder + getOrder"},
            "product_master": {"status": "not_implemented"},
            "inventory_api": {"status": "not_implemented"},
        },
        "notes": "当前库存页中的乐天销售统计使用受注API聚合，不是乐天商品API/在库API原始返回。",
    }


@app.get("/api/mapping/materials")
def get_mapping_materials(platform: str = "rakuten", days: int = 7):
    return {"success": False, "message": "映射资料已移动到同步任务反馈中", "rows": []}
    """
    映射资料中心：
    - rakuten: 从受注明细聚合 SKU -> systemSku 候选映射
    - yahoo: 预留
    """
    pf = (platform or "rakuten").strip().lower()
    if pf not in {"rakuten", "yahoo"}:
        return {"success": False, "message": "platform 仅支持 rakuten / yahoo", "rows": []}

    if pf == "yahoo":
        return {
            "success": True,
            "platform": "yahoo",
            "rows": [],
            "message": "Yahoo 映射资料接口待接入",
        }

    # rakuten
    try:
        days = max(1, min(int(days), 90))
    except Exception:
        days = 7

    client = RakutenApiClient()
    if not client.ping():
        return {"success": False, "platform": "rakuten", "rows": [], "message": "RAKUTEN_API_KEY / RAKUTEN_SHOP_ID 未配置"}

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    try:
        spu_tags, spu_warning = _load_spu_tag_values()
        orders = client.get_orders_detailed(start, end)
        agg: dict[str, dict] = {}
        for o in orders:
            for it in (o.get("items") or []):
                sku = str(it.get("manageNumber") or it.get("itemNumber") or "").strip()
                if not sku:
                    continue
                system_sku = str(it.get("systemSku") or "").strip()
                name = str(it.get("itemName") or "").strip()
                row = agg.get(sku)
                if not row:
                    row = {
                        "sku": sku,
                        "system_sku": system_sku,
                        "sample_name": name,
                    }
                    agg[sku] = row
                if not row.get("system_sku") and system_sku:
                    row["system_sku"] = system_sku
                if not row.get("sample_name") and name:
                    row["sample_name"] = name

        rows = []
        for v in agg.values():
            system_sku = v.get("system_sku") or ""
            missing_system_sku = not bool(system_sku)
            matched_tag_spu = _find_matching_tag(system_sku, spu_tags)
            tag_spu_exists = bool(matched_tag_spu)
            if missing_system_sku:
                status = "missing_system_sku"
                note = "システム連携用SKU番号 缺失"
            elif not tag_spu_exists:
                status = "missing_tag_spu"
                note = "SPU月别_DB.tag_spu 中不存在"
            else:
                status = "ok"
                note = ""
            rows.append(
                {
                    "sku": v["sku"],
                    "system_sku": system_sku,
                    "sample_name": v.get("sample_name") or "",
                    "tag_spu_exists": tag_spu_exists,
                    "matched_tag_spu": matched_tag_spu,
                    "status": status,
                    "note": note,
                }
            )
        rows.sort(key=lambda x: (x["status"] == "ok", x["system_sku"] == "", x["sku"]))
        missing_system_sku_count = sum(1 for row in rows if row["status"] == "missing_system_sku")
        missing_tag_spu_count = sum(1 for row in rows if row["status"] == "missing_tag_spu")
        ok_count = sum(1 for row in rows if row["status"] == "ok")

        return {
            "success": True,
            "platform": "rakuten",
            "range": {"start_date": start, "end_date": end},
            "summary": {
                "total": len(rows),
                "ok": ok_count,
                "missing_system_sku": missing_system_sku_count,
                "missing_tag_spu": missing_tag_spu_count,
                "spu_tag_count": len(spu_tags),
                "spu_warning": spu_warning,
            },
            "rows": rows,
            "message": f"已聚合 {len(rows)} 条 SKU 映射资料",
        }
    except Exception as e:
        return {"success": False, "platform": "rakuten", "rows": [], "message": str(e)}


@app.post("/api/ecommerce/amazon-keyword/add")
def api_amazon_keyword_add(data: dict):
    """添加关键词到 Master 表"""
    from src.features.ecommerce.amazon.keyword_tracker import add_keyword_to_master
    return add_keyword_to_master(
        brand=data.get("brand", ""),
        asin=data.get("asin", ""),
        product=data.get("product", ""),
        keyword=data.get("keyword", "")
    )


@app.post("/api/ecommerce/amazon-keyword/delete")
def api_amazon_keyword_delete(data: dict):
    """删除指定的关键词"""
    from src.features.ecommerce.amazon.keyword_tracker import delete_keyword_from_master
    return delete_keyword_from_master(
        asin=data.get("asin", ""),
        keyword=data.get("keyword", "")
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run_api:app", host="0.0.0.0", port=8000, reload=True)
