"""
Amazon 关键词自然位/広告位追踪。
从飞书 Sheet 读取 ASIN + 关键词，搜索 Amazon.co.jp，
解析目标 ASIN 在搜索结果中的自然排名和广告排名，写回当日列。
"""
from __future__ import annotations

import re
import random
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from src.core.config_manager import get_env
from src.features.feishu.bot_client import _get_tenant_access_token

# ---------------------------------------------------------------------------
# 配置（复用排名同步的部分配置）
# ---------------------------------------------------------------------------
LARK_HOST = (get_env("LARK_HOST") or "https://open.larksuite.com").rstrip("/")

HEADER_ROW = 1
DATA_START_ROW = 2
ASIN_COL = "A"
KEYWORD_COL = "B"
TYPE_COL = "C"
MAX_ROWS = int(get_env("MAX_ROWS") or "500")
MAX_PAGES = int(get_env("AMZ_KEYWORD_MAX_PAGES") or "3")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
]

LARK_RETRY = int(get_env("LARK_RETRY") or "3")
LARK_RETRY_BASE_SLEEP = float(get_env("LARK_RETRY_BASE_SLEEP") or "1.5")
AMZ_SLEEP_MIN = float(get_env("AMZ_SLEEP_MIN") or "2.0")
AMZ_SLEEP_MAX = float(get_env("AMZ_SLEEP_MAX") or "6.0")

JST = timezone(timedelta(hours=9))
AMZ_SESSION = requests.Session()

# 自然位 / 広告位 标识
TYPE_ORGANIC = "自然位"
TYPE_SPONSORED = "広告位"


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
# ASIN 解析
# ---------------------------------------------------------------------------
def _extract_asin(v) -> str | None:
    """从单元格值中提取 10 位 ASIN（支持链接或纯文本）。"""
    s = str(v).strip() if v is not None else ""
    m = re.search(r"/dp/([A-Z0-9]{10})", s)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z0-9]{10})\b", s)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Amazon 搜索结果解析
# ---------------------------------------------------------------------------
def _fetch_search_page(keyword: str, page: int = 1) -> str | None:
    """请求 Amazon.co.jp 搜索页面，返回 HTML 文本。"""
    url = f"https://www.amazon.co.jp/s?k={quote_plus(keyword)}&page={page}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    }
    try:
        r = AMZ_SESSION.get(url, headers=headers, timeout=25)
        if r.status_code != 200:
            print(f"[WARN] search HTTP {r.status_code} keyword={keyword} page={page}")
            return None
        html = r.text
        low = html.lower()
        if "captcha" in low or "ロボットではありません" in html:
            print(f"[WARN] CAPTCHA detected keyword={keyword} page={page}")
            return None
        return html
    except Exception as e:
        print(f"[WARN] search error keyword={keyword} page={page} error={e}")
        return None


def _parse_search_results(html: str) -> list[dict]:
    """
    解析搜索结果页，返回商品列表：
    [{"asin": "B0XXX", "position": 1, "is_sponsored": True/False}, ...]
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    organic_pos = 0
    sponsored_pos = 0

    # Amazon 搜索结果的商品卡片一般在 data-component-type="s-search-result" 的 div 中
    items = soup.select('[data-component-type="s-search-result"]')

    for item in items:
        asin = item.get("data-asin", "").strip()
        if not asin:
            continue

        # 判断是否广告：检查 AdHolder class 或 "スポンサー" 文字
        is_sponsored = False

        # 方法 1：检查 class 是否包含 AdHolder 或 sp-sponsored-result
        classes = " ".join(item.get("class", []))
        if "AdHolder" in classes or "sp-sponsored-result" in classes:
            is_sponsored = True

        # 方法 2：检查子元素中是否有 "スポンサー"（Sponsored）文字
        if not is_sponsored:
            sponsored_label = item.select_one('.puis-label-popover-default, .s-label-popover-default')
            if sponsored_label and "スポンサー" in sponsored_label.get_text():
                is_sponsored = True

        # 方法 3：上一层检查
        if not is_sponsored:
            item_text = item.get_text(" ", strip=True)[:200]
            if "スポンサー" in item_text:
                is_sponsored = True

        if is_sponsored:
            sponsored_pos += 1
            pos = sponsored_pos
        else:
            organic_pos += 1
            pos = organic_pos

        results.append({
            "asin": asin,
            "position": pos,
            "is_sponsored": is_sponsored,
        })

    return results


def search_keyword_positions(
    keyword: str,
    target_asins: set[str],
    max_pages: int = MAX_PAGES,
) -> dict[str, dict[str, int | None]]:
    """
    搜索关键词，返回每个目标 ASIN 的自然位和广告位。

    返回格式：
    {
        "B0XXXXXXX": {"organic": 5, "sponsored": None},
        "B0YYYYYYY": {"organic": None, "sponsored": 2},
    }
    未找到的 ASIN 也会包含在结果中，值为 None。
    """
    result: dict[str, dict[str, int | None]] = {
        asin: {"organic": None, "sponsored": None}
        for asin in target_asins
    }

    # 累计各页面的位置偏移
    organic_offset = 0
    sponsored_offset = 0

    for page in range(1, max_pages + 1):
        html = _fetch_search_page(keyword, page)
        if not html:
            break

        items = _parse_search_results(html)
        if not items:
            break  # 没有更多结果

        for item in items:
            asin = item["asin"]
            if asin in target_asins:
                if item["is_sponsored"]:
                    if result[asin]["sponsored"] is None:
                        result[asin]["sponsored"] = sponsored_offset + item["position"]
                else:
                    if result[asin]["organic"] is None:
                        result[asin]["organic"] = organic_offset + item["position"]

        # 更新偏移量（取最后一个项目的位置）
        page_organic = sum(1 for i in items if not i["is_sponsored"])
        page_sponsored = sum(1 for i in items if i["is_sponsored"])
        organic_offset += page_organic
        sponsored_offset += page_sponsored

        # 如果所有目标 ASIN 都找到了，提前退出
        all_found = all(
            (result[a]["organic"] is not None or result[a]["sponsored"] is not None)
            for a in target_asins
        )
        if all_found:
            break

        _sleep_jitter(AMZ_SLEEP_MIN, AMZ_SLEEP_MAX)

    return result


# ---------------------------------------------------------------------------
# 日期列管理
# ---------------------------------------------------------------------------
def _ensure_today_col(token: str, spreadsheet_token: str, sheet_id: str) -> str:
    """找到或创建当日列，返回列字母。"""
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


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run_keyword_tracking(sheet_title: str | None = None, dry_run: bool = False) -> dict:
    """
    执行 Amazon 关键词追踪。

    飞书 Sheet 布局：
      A列: ASIN | B列: 关键词 | C列: 自然位/広告位 | D列起: 日期列

    :param sheet_title: 飞书 Sheet 名称，默认 FEISHU_KEYWORD_SHEET_NAME 或 "KW追踪"
    :param dry_run: True 时只抓取不写入飞书
    :return: {"success": bool, "message": str, "results": list}
    """
    spreadsheet_token = get_env("FEISHU_SHEET_TOKEN") or ""
    if not spreadsheet_token:
        return {"success": False, "message": "未配置 FEISHU_SHEET_TOKEN", "results": []}

    app_id = get_env("FEISHU_APP_ID") or ""
    app_secret = get_env("FEISHU_APP_SECRET") or ""
    if not app_id or not app_secret:
        return {"success": False, "message": "未配置 FEISHU_APP_ID / FEISHU_APP_SECRET", "results": []}

    title = sheet_title or (get_env("FEISHU_KEYWORD_SHEET_NAME") or "KW追踪")

    try:
        token = _get_tenant_access_token()
        sheet_id = _resolve_sheet_id(token, spreadsheet_token, title)

        # 1. 如果不是 dry_run，确保当日列存在
        col = None
        if not dry_run:
            col = _ensure_today_col(token, spreadsheet_token, sheet_id)
            print(f"[INFO] today={_today_header_text()} col={col}")

        # 2. 读取 A/B/C 列数据
        end_row = DATA_START_ROW + MAX_ROWS - 1
        ranges = [
            f"{sheet_id}!{ASIN_COL}{DATA_START_ROW}:{ASIN_COL}{end_row}",
            f"{sheet_id}!{KEYWORD_COL}{DATA_START_ROW}:{KEYWORD_COL}{end_row}",
            f"{sheet_id}!{TYPE_COL}{DATA_START_ROW}:{TYPE_COL}{end_row}",
        ]
        data = _batch_get(token, spreadsheet_token, ranges)
        vrs = data.get("data", {}).get("valueRanges", [])

        asin_rows = (vrs[0].get("values") if len(vrs) > 0 else None) or []
        kw_rows = (vrs[1].get("values") if len(vrs) > 1 else None) or []
        type_rows = (vrs[2].get("values") if len(vrs) > 2 else None) or []

        # 3. 构建 (ASIN, 关键词) → [{row_no, type}] 映射
        #    每个关键词有两行：自然位和広告位
        keyword_tasks: dict[str, set[str]] = {}  # keyword → {asin1, asin2, ...}
        row_map: list[dict] = []  # [{row_no, asin, keyword, type}, ...]

        for i in range(max(len(asin_rows), len(kw_rows), len(type_rows))):
            row_no = DATA_START_ROW + i
            raw_asin = (asin_rows[i][0] if i < len(asin_rows) and asin_rows[i] else "")
            raw_kw = (kw_rows[i][0] if i < len(kw_rows) and kw_rows[i] else "")
            raw_type = (type_rows[i][0] if i < len(type_rows) and type_rows[i] else "")

            asin = _extract_asin(raw_asin)
            keyword = str(raw_kw).strip() if raw_kw else ""
            row_type = str(raw_type).strip() if raw_type else ""

            if not asin or not keyword or row_type not in (TYPE_ORGANIC, TYPE_SPONSORED):
                continue

            row_map.append({
                "row_no": row_no,
                "asin": asin,
                "keyword": keyword,
                "type": row_type,
            })

            if keyword not in keyword_tasks:
                keyword_tasks[keyword] = set()
            keyword_tasks[keyword].add(asin)

        if not keyword_tasks:
            return {"success": True, "message": "Sheet 中无有效关键词数据", "results": []}

        print(f"[INFO] 共 {len(keyword_tasks)} 个关键词，{len(row_map)} 行数据")

        # 4. 逐关键词搜索
        search_cache: dict[str, dict] = {}  # keyword → {asin: {organic, sponsored}}

        for keyword, target_asins in keyword_tasks.items():
            print(f"[INFO] 搜索关键词: {keyword} (目标 ASIN: {target_asins})")
            positions = search_keyword_positions(keyword, target_asins)
            search_cache[keyword] = positions

            for asin, pos in positions.items():
                print(f"  {asin}: organic={pos['organic']} sponsored={pos['sponsored']}")

            _sleep_jitter(AMZ_SLEEP_MIN, AMZ_SLEEP_MAX)

        # 5. 构建更新
        updates: list[dict] = []
        result_log: list[dict] = []

        for entry in row_map:
            asin = entry["asin"]
            keyword = entry["keyword"]
            row_no = entry["row_no"]
            row_type = entry["type"]

            positions = search_cache.get(keyword, {}).get(asin, {})

            if row_type == TYPE_ORGANIC:
                value = positions.get("organic")
            else:
                value = positions.get("sponsored")

            cell_value = value if value is not None else "N/A"

            result_log.append({
                "row": row_no,
                "asin": asin,
                "keyword": keyword,
                "type": row_type,
                "position": cell_value,
            })

            if col and not dry_run:
                updates.append({
                    "range": f"{sheet_id}!{col}{row_no}:{col}{row_no}",
                    "values": [[cell_value]],
                })

        # 6. 写入飞书
        if updates and not dry_run:
            _batch_update(token, spreadsheet_token, updates)
            msg = f"已更新 {len(updates)} 个单元格（{len(keyword_tasks)} 个关键词）"
        elif dry_run:
            msg = f"[DRY-RUN] 共 {len(result_log)} 条结果（未写入）"
        else:
            msg = "无需更新"

        print(f"[DONE] {msg}")
        return {"success": True, "message": msg, "results": result_log}

    except Exception as e:
        return {"success": False, "message": str(e), "results": []}
