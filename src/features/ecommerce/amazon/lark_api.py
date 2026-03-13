import time
import random
from datetime import datetime, timezone, timedelta
import requests

from src.core.config_manager import get_env

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
LARK_HOST = (get_env("LARK_HOST") or "https://open.larksuite.com").rstrip("/")
LARK_RETRY = int(get_env("LARK_RETRY") or "3")
LARK_RETRY_BASE_SLEEP = float(get_env("LARK_RETRY_BASE_SLEEP") or "1.5")
HEADER_ROW = 1
JST = timezone(timedelta(hours=9))

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
