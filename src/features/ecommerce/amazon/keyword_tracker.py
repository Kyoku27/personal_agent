import os
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.core.config_manager import get_env
from src.features.feishu.bot_client import _get_tenant_access_token
from .lark_api import (
    HEADER_ROW,
    _batch_get,
    _batch_update,
    _resolve_sheet_id,
)


JST = timezone(timedelta(hours=9))
MAX_RANK = 999
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =========================
# 读取关键词配置（从飞书 Master 表）
# =========================
def load_keywords_from_lark(sheet_title: str = "Master") -> list[dict]:
    """
    从飞书表格读取关键词配置。
    要求飞书对应的子表名称为 'Master'（或其他传入的 title），
    且第一行为表头，包含：brand, asin, product, keyword (大小写和顺序不限)。
    """
    spreadsheet_token = (
        get_env("FEISHU_KEYWORD_SHEET_TOKEN")
        or get_env("FEISHU_SHEET_TOKEN")
        or ""
    )
    if not spreadsheet_token:
        print("[WARN] FEISHU_SHEET_TOKEN not found, cannot load keywords")
        return []

    try:
        from .lark_api import _resolve_sheet_id, _batch_get

        token = _get_tenant_access_token()
        try:
            sheet_id = _resolve_sheet_id(token, spreadsheet_token, sheet_title)
        except Exception as e:
            print(f"[ERROR] Sheet '{sheet_title}' not found in Lark, please create it: {e}")
            return []

        # 获取数据 (A到Z列，最大支持1000行)
        rng = f"{sheet_id}!A1:Z1000"
        data = _batch_get(token, spreadsheet_token, [rng])
        vr = data.get("data", {}).get("valueRanges", [])
        rows = (vr[0].get("values") if vr else []) or []

        if len(rows) <= 1:
            print(f"[WARN] Sheet '{sheet_title}' is empty or only contains header")
            return []

        headers = [str(h).strip().lower() for h in rows[0]]
        
        try:
            idx_brand = headers.index("brand") if "brand" in headers else -1
            idx_asin = headers.index("asin")
            idx_product = headers.index("product") if "product" in headers else -1
            idx_keyword = headers.index("keyword")
        except ValueError as e:
            print(f"[ERROR] Master sheet header must contain 'asin' and 'keyword' columns: {e}")
            return []

        def _extract_text(cell) -> str:
            if cell is None:
                return ""
            if isinstance(cell, list):
                # Lark often returns a list of items for a cell
                return "".join(_extract_text(item) for item in cell)
            if isinstance(cell, dict):
                return str(cell.get("text", "") or "")
            s = str(cell).strip()
            return s if s.lower() != "none" else ""

        records = []
        last_brand = ""
        last_asin = ""
        last_product = ""

        # rows[0] is header, rows[1:] are data
        for row in rows[1:]:
            # 将行数据补齐匹配表头长
            row = row + [""] * (len(headers) - len(row))
            
            asin = _extract_text(row[idx_asin])
            if not asin:
                asin = last_asin
            else:
                last_asin = asin

            brand = _extract_text(row[idx_brand]) if idx_brand != -1 else ""
            if not brand:
                brand = last_brand
            else:
                last_brand = brand

            product = _extract_text(row[idx_product]) if idx_product != -1 else ""
            if not product:
                product = last_product
            else:
                last_product = product
                
            keyword = _extract_text(row[idx_keyword])
            
            # If both ASIN and Keyword are empty, we've likely hit the end of data rows
            if not asin and not keyword:
                continue

            # Need at least a keyword to track
            if not keyword:
                continue

            records.append({
                "brand": brand,
                "asin": asin,
                "product": product,
                "keyword": keyword
            })
            
        print(f"[INFO] Successfully loaded {len(records)} keywords from Lark Sheet '{sheet_title}'.")
        return records

    except Exception as e:
        print(f"[ERROR] Failed to load Master from Lark: {e}")
        return []


# =========================
# Amazon 搜索页面（带简单容错）
# =========================
def get_amazon_page(keyword: str, timeout: int = 15) -> str | None:
    url = f"https://www.amazon.co.jp/s?k={keyword}"
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if r.status_code == 503:
            print(f"[WARN] Amazon returned 503 (Anti-scraping) for keyword='{keyword}'")
            return None
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"[WARN] Failed to get Amazon page for keyword='{keyword}': {e}")
        return None


# =========================
# 解析自然位排名
# =========================
def extract_organic_rank(soup: BeautifulSoup, asin: str, max_rank: int = MAX_RANK) -> int:
    results = soup.select("div[data-asin]")
    rank = 1

    for r in results:
        asin_found = r.get("data-asin")
        if not asin_found:
            continue

        # 跳过广告位
        classes = " ".join(r.get("class", []))
        if "AdHolder" in classes or "sp-sponsored-result" in classes:
            continue

        if asin_found == asin:
            return rank

        rank += 1

    return max_rank


# =========================
# 解析广告位排名
# =========================
def extract_ad_rank(soup: BeautifulSoup, asin: str, max_rank: int = MAX_RANK) -> int:
    # Amazon 广告结果组件可能不一样，这里使用通用包含 sp-sponsored-result
    ads = soup.select("div[data-component-type='s-search-result'], div.AdHolder")

    rank = 1
    for ad in ads:
        asin_found = ad.get("data-asin")
        if not asin_found:
            continue

        is_sponsored = False
        classes = " ".join(ad.get("class", []))
        if "AdHolder" in classes or "sp-sponsored-result" in classes:
            is_sponsored = True
        else:
            sponsored_label = ad.select_one(
                ".puis-label-popover-default, .s-label-popover-default"
            )
            if sponsored_label and "スポンサー" in sponsored_label.get_text():
                is_sponsored = True
            elif "スポンサー" in ad.get_text(" ", strip=True)[:200]:
                is_sponsored = True

        if is_sponsored:
            if asin_found == asin:
                return rank
            rank += 1

    return max_rank


# =========================
# 对外接口：单独获取自然位 / 广告位
# =========================
def get_organic_rank(keyword: str, asin: str) -> int:
    html = get_amazon_page(keyword)
    if not html:
        return MAX_RANK

    soup = BeautifulSoup(html, "html.parser")
    return extract_organic_rank(soup, asin)


def get_ad_rank(keyword: str, asin: str) -> int:
    html = get_amazon_page(keyword)
    if not html:
        return MAX_RANK

    soup = BeautifulSoup(html, "html.parser")
    return extract_ad_rank(soup, asin)


# =========================
# 一次请求同时获取自然位 + 广告位
# =========================
def get_ranks(keyword: str, asin: str) -> tuple[int, int]:
    html = get_amazon_page(keyword)
    if not html:
        return MAX_RANK, MAX_RANK

    soup = BeautifulSoup(html, "html.parser")
    organic_rank = extract_organic_rank(soup, asin)
    ad_rank = extract_ad_rank(soup, asin)
    return organic_rank, ad_rank


# =========================
# 生成记录
# =========================
def create_log(brand, asin, product, keyword, rank_type, rank):
    today = datetime.now(JST).strftime("%Y-%m-%d")
    return {
        "date": today,
        "brand": brand,
        "asin": asin,
        "product": product,
        "keyword": keyword,
        "rank_type": rank_type,
        "rank": rank,
    }


# =========================
# 写入飞书 Sheet（纵向追加）
# =========================
def _append_logs_to_lark(
    logs: list[dict[str, Any]], sheet_title: str | None = None
) -> dict[str, Any]:
    # 优先使用关键词专用的 Sheet token，其次回退到通用 FEISHU_SHEET_TOKEN
    spreadsheet_token = (
        get_env("FEISHU_KEYWORD_SHEET_TOKEN")
        or get_env("FEISHU_SHEET_TOKEN")
        or ""
    )
    if not spreadsheet_token:
        return {
            "success": False,
            "message": "未配置 FEISHU_SHEET_TOKEN",
            "updated_cells": 0,
        }

    title = sheet_title or (get_env("FEISHU_KEYWORD_SHEET_NAME") or "KW追踪")

    try:
        token = _get_tenant_access_token()
        sheet_id = _resolve_sheet_id(token, spreadsheet_token, title)

        # 读取 A 列，计算当前已有行数，用于确定追加的起始行
        rng = f"{sheet_id}!A:A"
        data = _batch_get(token, spreadsheet_token, [rng])
        vr = data.get("data", {}).get("valueRanges", [])
        rows = (vr[0].get("values") if vr else None) or []

        # values 的长度 = 已用行数（含表头），下一行 = len + 1
        next_row = len(rows) + 1
        if next_row <= HEADER_ROW:
            next_row = HEADER_ROW + 1

        values = [
            [
                log["date"],
                log["brand"],
                log["asin"],
                log["product"],
                log["keyword"],
                log["rank_type"],
                log["rank"],
            ]
            for log in logs
        ]

        start_row = next_row
        end_row = next_row + len(values) - 1
        cell_range = f"{sheet_id}!A{start_row}:G{end_row}"

        _batch_update(
            token,
            spreadsheet_token,
            [
                {
                    "range": cell_range,
                    "values": values,
                }
            ],
        )
        return {
            "success": True,
            "message": f"已追加 {len(values)} 行到飞书 Sheet '{title}'",
            "updated_cells": len(values) * 7,
        }
    except Exception as e:
        return {"success": False, "message": str(e), "updated_cells": 0}


# =========================
# 对外入口：关键词追踪并写入飞书
# =========================
def run_keyword_tracking(
    *, sheet_title: str | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """
    抓取关键词排名，并按品牌分 Sheet 写入：
    - brand 列为空的归到 "UNKNOWN"
    - 每个 brand 对应一个 Sheet（同一个 spreadsheet 下）
    """
    keywords = load_keywords_from_lark()
    if not keywords:
        return {
            "success": False,
            "message": "未能从飞书 Master 表中读取到关键词配置，退出执行",
            "updated_cells": 0,
        }

    by_brand: dict[str, list[dict[str, Any]]] = {}

    for k in keywords:
        brand = (k.get("brand") or "").strip() or "UNKNOWN"
        asin = k.get("asin", "")
        product = k.get("product", "")
        keyword = k.get("keyword", "")

        if not asin or not keyword:
            continue

        print("checking:", brand, "|", asin, "|", keyword)

        organic_rank, ad_rank = get_ranks(keyword, asin)

        by_brand.setdefault(brand, []).append(
            create_log(
                brand,
                asin,
                product,
                keyword,
                "organic",
                organic_rank,
            )
        )

        by_brand.setdefault(brand, []).append(
            create_log(
                brand,
                asin,
                product,
                keyword,
                "ad",
                ad_rank,
            )
        )

        # 简单节流，避免请求过快
        time.sleep(random.uniform(2, 4))

    all_logs = [log for logs in by_brand.values() for log in logs]

    if dry_run:
        # 仅返回结果，不写入飞书
        results = [
            {
                "row": i + 1,
                "brand": log["brand"],
                "asin": log["asin"],
                "keyword": log["keyword"],
                "type": log["rank_type"],
                "position": log["rank"],
            }
            for i, log in enumerate(all_logs)
        ]
        return {
            "success": True,
            "message": f"dry-run: 共生成 {len(all_logs)} 条排名记录，{len(by_brand)} 个品牌",
            "updated_cells": 0,
            "results": results,
        }

    if not all_logs:
        return {
            "success": True,
            "message": "没有有效的关键词/ASIN 记录，未写入飞书",
            "updated_cells": 0,
        }

    # 書き込み先 sheet 名の決定:
    # --sheet 指定あり → そのまま使用
    # 指定なし → .env の FEISHU_KEYWORD_SHEET_NAME (デフォルト "KW追踪") に全ブランドまとめて書き込み
    dest_sheet = sheet_title or (get_env("FEISHU_KEYWORD_SHEET_NAME") or "KW追踪")

    total_cells = 0
    res = _append_logs_to_lark(all_logs, sheet_title=dest_sheet)
    print(f"-> {res.get('message')}")
    total_cells += int(res.get("updated_cells") or 0)

    return {
        "success": True,
        "message": f"已按品牌写入 {len(by_brand)} 个 Sheet，总计 {total_cells} 个单元格",
        "updated_cells": total_cells,
    }


# =========================
# 兼容：本地运行仍写 CSV
# =========================
def main():
    keywords = load_keywords_from_lark()
    if not keywords:
        print("未读取到关键词配置，退出。")
        return

    logs: list[dict[str, Any]] = []

    for k in keywords:
        brand = k.get("brand", "")
        asin = k.get("asin", "")
        product = k.get("product", "")
        keyword = k.get("keyword", "")

        if not asin or not keyword:
            continue

        print("checking (CSV):", asin, "|", keyword)

        organic_rank, ad_rank = get_ranks(keyword, asin)

        logs.append(
            create_log(
                brand,
                asin,
                product,
                keyword,
                "organic",
                organic_rank,
            )
        )

        logs.append(
            create_log(
                brand,
                asin,
                product,
                keyword,
                "ad",
                ad_rank,
            )
        )

        time.sleep(random.uniform(2, 4))

    if not logs:
        print("no logs, skip csv")
        return

    df = pd.DataFrame(logs)
    log_path = os.path.join(BASE_DIR, "rank_log.csv")

    header = not os.path.exists(log_path)
    df.to_csv(log_path, mode="a", header=header, index=False)

    print("done")
