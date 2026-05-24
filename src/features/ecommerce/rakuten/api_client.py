import base64
import os
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.core.config_manager import get_env


def _store_env_prefix(store_id: str | None) -> str:
    if not store_id or store_id == "default":
        return ""
    return re.sub(r"[^A-Z0-9]+", "_", store_id.upper()).strip("_")


def _get_store_env(
    prefix: str,
    suffix: str,
    fallback_key: str,
    default: str,
    require_store_specific: bool = False,
) -> str:
    if prefix:
        store_key = f"RAKUTEN_{prefix}_{suffix}"
        value = get_env(store_key, "") or ""
        if value:
            return value
        if require_store_specific:
            raise RuntimeError(f"{store_key} is not configured")
    return get_env(fallback_key, default) or default


@dataclass
class RakutenApiClient:
    api_key: str = ""      # "serviceSecret:licenseKey" の形式
    shop_id: str = ""
    shop_id: str = ""
    base_url: str = ""
    store_id: str = "default"

    def __post_init__(self):
        prefix = _store_env_prefix(self.store_id)
        require_store_specific = bool(prefix)
        self.api_key = self.api_key or _get_store_env(
            prefix,
            "API_KEY",
            "RAKUTEN_API_KEY",
            "",
            require_store_specific=require_store_specific,
        )
        self.shop_id = self.shop_id or _get_store_env(
            prefix,
            "SHOP_ID",
            "RAKUTEN_SHOP_ID",
            "",
            require_store_specific=require_store_specific,
        )
        self.base_url = self.base_url or _get_store_env(prefix, "BASE_URL", "RAKUTEN_BASE_URL", "https://api.rms.rakuten.co.jp")

    def _get_auth_header(self) -> str:
        """
        楽天 RMS API 認証ヘッダーを生成する。
        環境変数 RAKUTEN_API_KEY は "serviceSecret:licenseKey" の形式で設定する。
        それを Base64 エンコードして "ESA {base64}" の形にする。
        """
        encoded = base64.b64encode(self.api_key.encode("utf-8")).decode("utf-8")
        return f"ESA {encoded}"

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/json; charset=utf-8",
        }

    def ping(self) -> bool:
        """API 接続テスト。"""
        return bool(self.api_key and self.shop_id)

    def search_orders(self, start_date: str, end_date: str, page: int = 1) -> dict[str, Any]:
        """
        楽天 RMS 受注検索 API を呼び出す。
        
        Args:
            start_date: "YYYY-MM-DD" 形式
            end_date:   "YYYY-MM-DD" 形式
            page:       ページ番号 (1始まり)
        
        Returns:
            楽天 API のレスポンス JSON (dict)
        """
        endpoint = f"{self.base_url}/es/2.0/order/searchOrder/"
        # datetimeフォーマットに変換
        start_dt = f"{start_date}T00:00:00+0900"
        end_dt = f"{end_date}T23:59:59+0900"

        payload = {
            "dateType": 1,              # 1: 注文日で検索
            "startDatetime": start_dt,
            "endDatetime": end_dt,
            "PaginationRequestModel": {
                "requestRecordsAmount": 100,
                "requestPage": page,
                "SortModelList": [{"sortColumn": 1, "sortDirection": 1}]
            }
        }

        resp = requests.post(endpoint, headers=self._get_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_order_items(self, order_numbers: list[str]) -> dict[str, Any]:
        """
        受注番号リストから各商品の詳細（SKU管理番号、数量、金額）を取得する。
        
        Args:
            order_numbers: 受注番号のリスト
        
        Returns:
            楽天 API のレスポンス JSON (dict)
        """
        endpoint = f"{self.base_url}/es/2.0/order/getOrder/"
        payload = {
            "orderNumberList": order_numbers,
            "version": 8
        }
        resp = requests.post(endpoint, headers=self._get_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_orders_detailed(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Fetch Rakuten orders and flatten item details for Bitable sync."""
        if not self.api_key:
            raise RuntimeError(f"Rakuten API key is not configured for store '{self.store_id}'")

        order_numbers: list[str] = []
        seen_order_numbers: set[str] = set()
        for range_start, range_end in self._iter_search_ranges(start_date, end_date):
            page = 1
            while True:
                result = self.search_orders(range_start, range_end, page)
                for order_number in result.get("orderNumberList") or []:
                    if order_number not in seen_order_numbers:
                        order_numbers.append(order_number)
                        seen_order_numbers.add(order_number)
                pagination = result.get("PaginationResponseModel") or {}
                total_pages = int(pagination.get("totalPages") or 1)
                if page >= total_pages:
                    break
                page += 1

        orders: list[dict[str, Any]] = []
        for i in range(0, len(order_numbers), 100):
            detail = self.get_order_items(order_numbers[i:i + 100])
            for raw in detail.get("OrderModelList") or []:
                order = {
                    "orderNumber": raw.get("orderNumber"),
                    "orderDatetime": raw.get("orderDatetime"),
                    "requestPrice": raw.get("requestPrice"),
                    "totalPrice": raw.get("totalPrice"),
                    "shippingFee": raw.get("postagePrice") or raw.get("shippingFee"),
                    "ordererName": raw.get("ordererName") or raw.get("ordererName1"),
                    "ordererPrefecture": raw.get("ordererPrefecture"),
                    "settlementMethod": raw.get("settlementMethod"),
                    "orderStatus": raw.get("orderStatus"),
                    "orderProgressCode": raw.get("orderProgressCode") or raw.get("orderProgress"),
                    "items": [],
                }
                for package in raw.get("PackageModelList") or []:
                    for item in package.get("ItemModelList") or []:
                        sku_model = (item.get("SkuModelList") or [{}])[0]
                        order["items"].append({
                            "manageNumber": item.get("manageNumber"),
                            "itemNumber": item.get("itemNumber"),
                            "itemName": item.get("itemName"),
                            "systemSku": sku_model.get("merchantDefinedSkuId") or item.get("systemSku"),
                            "variantId": sku_model.get("variantId"),
                            "units": item.get("units"),
                            "price": item.get("price"),
                        })
                orders.append(order)
        return orders

    @staticmethod
    def _iter_search_ranges(start_date: str, end_date: str, max_days: int = 31) -> list[tuple[str, str]]:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        ranges: list[tuple[str, str]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=max_days - 1), end)
            ranges.append((cursor.isoformat(), chunk_end.isoformat()))
            cursor = chunk_end + timedelta(days=1)
        return ranges

    def get_sales_data(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """
        指定日付範囲の全注文を取得し、SKU（商品管理番号）別に集計して返す。
        
        Returns:
            [{"sku": "PD50", "sales": 49500.0, "order_count": 1}, ...]
        """
        if not self.api_key:
            print("⚠️ Rakuten API キーが未設定。モックデータを使用します。")
            return [
                {"sku": "PD50", "sales": 49500.0, "order_count": 1},
                {"sku": "PD60", "sales": 49500.0, "order_count": 1},
            ]

        print(f"🔍 楽天受注検索中: {start_date} 〜 {end_date}")
        
        # Step 1: 受注番号一覧を取得（全ページ）
        all_order_numbers: list[str] = []
        page = 1
        while True:
            result = self.search_orders(start_date, end_date, page)
            # エラーチェック
            if result.get("MessageModelList"):
                for msg in result["MessageModelList"]:
                    print(f"  API メッセージ: {msg.get('messageType')} - {msg.get('message')}")

            order_model_list = result.get("orderNumberList", [])
            if not order_model_list:
                break

            all_order_numbers.extend(order_model_list)
            
            # ページング
            pagination = result.get("PaginationResponseModel", {})
            total_pages = pagination.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1

        if not all_order_numbers:
            print("  受注なし（指定期間）")
            return []

        print(f"  受注番号 {len(all_order_numbers)} 件取得。詳細を取得中...")

        # Step 2: 受注詳細から SKU別に集計（100件ずつ分割）
        sku_summary: dict[str, dict[str, Any]] = {}
        chunk_size = 100
        for i in range(0, len(all_order_numbers), chunk_size):
            chunk = all_order_numbers[i:i + chunk_size]
            detail_resp = self.get_order_items(chunk)

            for order in detail_resp.get("OrderModelList", []):
                for package in order.get("PackageModelList", []):
                    for item in package.get("ItemModelList", []):
                        # 商品管理番号 = SKU に相当
                        sku = item.get("manageNumber") or item.get("itemNumber") or "UNKNOWN"
                        price = float(item.get("price", 0))
                        qty = int(item.get("units", 1))

                        if sku not in sku_summary:
                            sku_summary[sku] = {"sku": sku, "sales": 0.0, "order_count": 0}
                        sku_summary[sku]["sales"] += price * qty
                        sku_summary[sku]["order_count"] += qty

        result_list = list(sku_summary.values())
        print(f"  SKU {len(result_list)} 種類に集計完了。")
        return result_list
