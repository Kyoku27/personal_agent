"""
Amazon 日本站排名同步到飞书电子表格。
从飞书 Sheet 的 A 列读取 ASIN，抓取 Amazon 排名，写回当日列和 F 列（小类目）。
"""
from __future__ import annotations

import re
import random
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

from src.core.config_manager import get_env
from src.features.feishu.bot_client import _get_tenant_access_token

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
LARK_HOST = (get_env("LARK_HOST") or "https://open.larksuite.com").rstrip("/")

HEADER_ROW = 1
DATA_START_ROW = 2
ASIN_COL = "A"
AMZ_CAT_COL = "F"
MAX_ROWS = int(get_env("MAX_ROWS") or "200")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
]

LARK_RETRY = int(get_env("LARK_RETRY") or "3")
LARK_RETRY_BASE_SLEEP = float(get_env("LARK_RETRY_BASE_SLEEP") or "1.5")
AMZ_TRY = int(get_env("AMZ_TRY") or "2")
AMZ_SLEEP_MIN = float(get_env("AMZ_SLEEP_MIN") or "2.0")
AMZ_SLEEP_MAX = float(get_env("AMZ_SLEEP_MAX") or "6.0")

JST = timezone(timedelta(hours=9))
AMZ_SESSION = requests.Session()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _sleep_jitter(a: float = 1.2, b: float = 2.8) -> None:
    time.sleep(random.uniform(a, b))


def _today_header_text() -> str:
    now = datetime.now(JST)
    return f"{now.month}月{now.day}日"


def _num_to_col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(r + ord("A")) + s
    return s


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _request_with_retry(fn, *, max_retry=LARK_RETRY, base_sleep=LARK_RETRY_BASE_SLEEP):
    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            t = base_sleep * (1.6 ** (attempt - 1)) + random.uniform(0, 0.8)
            print(f"[WARN] retry {attempt}/{max_retry} error={e} sleep={t:.1f}s")
            time.sleep(t)
    raise last_err


# ---------------------------------------------------------------------------
# Lark Sheet API
# ---------------------------------------------------------------------------
def _list_sheets(token: str, spreadsheet_token: str) -> list[dict]:
    url = f"{LARK_HOST}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"

    def _call():
        r = requests.get(url, headers=_headers(token), timeout=20)
        if r.status_code != 200:
            try:
                j = r.json()
            except Exception:
                j = r.text
            raise RuntimeError(f"[LARK][list_sheets] status={r.status_code} body={j}")
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"[LARK][list_sheets] {data}")
        return data.get("data", {}).get("sheets", [])

    return _request_with_retry(_call)


def _resolve_sheet_id(token: str, spreadsheet_token: str, title: str) -> str:
    sheets = _list_sheets(token, spreadsheet_token)
    for s in sheets:
        if s.get("title") == title:
            return s.get("sheet_id")
    titles = [x.get("title") for x in sheets]
    raise RuntimeError(f"[LARK] sheet title not found: '{title}'. existing titles={titles}")


def _batch_get(token: str, spreadsheet_token: str, ranges: list[str]) -> dict:
    url = f"{LARK_HOST}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get"

    def _call():
        r = requests.get(url, headers=_headers(token), params={"ranges": ranges}, timeout=30)
        if r.status_code != 200:
            try:
                j = r.json()
            except Exception:
                j = r.text
            raise RuntimeError(f"[LARK][batch_get] status={r.status_code} body={j}")
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"[LARK][batch_get] {data}")
        return data

    return _request_with_retry(_call)


def _batch_update(token: str, spreadsheet_token: str, updates: list[dict]) -> dict:
    url = f"{LARK_HOST}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_update"

    def _call():
        body = {"valueInputOption": "RAW", "valueRanges": updates}
        r = requests.post(url, headers=_headers(token), json=body, timeout=30)
        if r.status_code != 200:
            try:
                j = r.json()
            except Exception:
                j = r.text
            raise RuntimeError(f"[LARK][batch_update] status={r.status_code} body={j}")
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"[LARK][batch_update] {data}")
        return data

    return _request_with_retry(_call)


# ---------------------------------------------------------------------------
# ASIN 解析 / Amazon 抓取
# ---------------------------------------------------------------------------
def _extract_asin(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    m = re.search(r"/dp/([A-Z0-9]{10})", s)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z0-9]{10})\b", s)
    if m:
        return m.group(1)
    return None


def _ensure_today_col(token: str, spreadsheet_token: str, sheet_id: str) -> str:
    rng = f"{sheet_id}!A{HEADER_ROW}:ZZ{HEADER_ROW}"
    data = _batch_get(token, spreadsheet_token, [rng])
    vr = data.get("data", {}).get("valueRanges", [])
    row = ((vr[0].get("values") if vr else None) or [[]])[0]
    today = _today_header_text()
    last = 0
    for i, v in enumerate(row):
        sv = str(v).strip() if v is not None else ""
        if sv:
            last = i + 1
        if sv == today:
            return _num_to_col(i + 1)
    target = last + 1
    col = _num_to_col(target)
    _batch_update(token, spreadsheet_token, [{
        "range": f"{sheet_id}!{col}{HEADER_ROW}:{col}{HEADER_ROW}",
        "values": [[today]],
    }])
    return col


def _fetch_rank(asin: str) -> tuple[str | None, str | None, str]:
    url = f"https://www.amazon.co.jp/dp/{asin}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    }
    r = AMZ_SESSION.get(url, headers=headers, timeout=25)
    if r.status_code == 403:
        return None, None, "HTTP_403"
    if r.status_code != 200:
        return None, None, f"HTTP_{r.status_code}"
    html = r.text
    low = html.lower()
    if "captcha" in low or "ロボットではありません" in html:
        return None, None, "CAPTCHA?"
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    key = "Amazon 売れ筋ランキング"
    idx = text.find(key)
    segment = text[idx: idx + 2500] if idx != -1 else text
    matches = re.findall(r"([^\n]{2,80})\s*[\-–—－]\s*(\d[\d,]*)\s*位", segment)
    if not matches:
        return None, None, "RANK_N/A"
    if len(matches) >= 2:
        sub_cat, sub_rank = matches[1]
        return sub_rank.replace(",", ""), sub_cat.strip(), "OK"
    main_cat, main_rank = matches[0]
    return main_rank.replace(",", ""), main_cat.strip(), "OK"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run_amazon_rank_sync_generator(sheet_title: str | None = None):
    """
    流式的 Amazon 排名同步，实时 yield 进度供前端显示与手动取消。
    """
    spreadsheet_token = get_env("FEISHU_SHEET_TOKEN") or ""
    if not spreadsheet_token:
        yield {"type": "error", "message": "未配置 FEISHU_SHEET_TOKEN", "updated_cells": 0}
        return

    app_id = get_env("FEISHU_APP_ID") or ""
    app_secret = get_env("FEISHU_APP_SECRET") or ""
    if not app_id or not app_secret:
        yield {"type": "error", "message": "未配置 FEISHU_APP_ID / FEISHU_APP_SECRET", "updated_cells": 0}
        return

    title = sheet_title or (get_env("FEISHU_SHEET_NAME") or f"{datetime.now(JST).month}月")
    yield {"type": "progress", "message": f"正在解析飞书 Sheet '{title}' 数据..."}

    try:
        token = _get_tenant_access_token()
        sheet_id = _resolve_sheet_id(token, spreadsheet_token, title)
        col = _ensure_today_col(token, spreadsheet_token, sheet_id)
        
        end_row = DATA_START_ROW + MAX_ROWS - 1
        asin_rng = f"{sheet_id}!{ASIN_COL}{DATA_START_ROW}:{ASIN_COL}{end_row}"
        data = _batch_get(token, spreadsheet_token, [asin_rng])
        vr = data.get("data", {}).get("valueRanges", [])
        rows = (vr[0].get("values") if vr else None) or []

        updates: list[dict] = []
        valid_count = 0
        for r in rows:
            if _extract_asin(r[0] if r else ""):
                valid_count += 1
                
        yield {"type": "progress", "message": f"共读取到 {valid_count} 个需要抓取的 ASIN，开始查询..."}

        for i, r in enumerate(rows):
            # 每个循环内部的 yield 回调
            yield {"type": "heartbeat"}

            row_no = DATA_START_ROW + i
            asin = _extract_asin(r[0] if r else "")
            if not asin:
                continue
                
            val, cat, status = None, None, "RANK_N/A"
            for attempt in range(AMZ_TRY):
                val, cat, status = _fetch_rank(asin)
                if val:
                    break
                # 如果没抓到，给前端发个状态说明在重试
                if attempt < AMZ_TRY - 1:
                    yield {"type": "progress", "message": f"[{row_no}] {asin} 暂时没有排名，稍作重试..."}
                    time.sleep(random.uniform(AMZ_SLEEP_MIN, AMZ_SLEEP_MAX))
            
            updates.append({
                "range": f"{sheet_id}!{col}{row_no}:{col}{row_no}",
                "values": [[val or status]],
            })
            if status == "OK" and cat:
                updates.append({
                    "range": f"{sheet_id}!{AMZ_CAT_COL}{row_no}:{AMZ_CAT_COL}{row_no}",
                    "values": [[cat]],
                })
            
            log_str = f"行 {row_no} | ASIN {asin} | 排名: {val or status} | 分类: {cat if (status == 'OK' and cat) else ''}"
            print(log_str)
            yield {"type": "progress", "message": log_str}
            
            _sleep_jitter()

        if updates:
            yield {"type": "progress", "message": "全部抓取完毕，正在将结果统一写回飞书..."}
            _batch_update(token, spreadsheet_token, updates)
            yield {"type": "done", "success": True, "message": f"已更新 {len(updates)} 个单元格", "updated_cells": len(updates)}
        else:
            yield {"type": "done", "success": True, "message": "无 ASIN 需要更新", "updated_cells": 0}

    except Exception as e:
        yield {"type": "error", "message": str(e), "updated_cells": 0}

# 兼容旧同步方法的壳
def run_amazon_rank_sync(sheet_title: str | None = None) -> dict:
    result = {"success": False, "message": "Unknown error", "updated_cells": 0}
    for event in run_amazon_rank_sync_generator(sheet_title):
        if event["type"] in ("done", "error"):
            result = {
                "success": event.get("success", False) or event["type"] == "done",
                "message": event.get("message", ""),
                "updated_cells": event.get("updated_cells", 0)
            }
    return result
