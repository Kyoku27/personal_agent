import base64
import os
import requests
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from src.core.config_manager import get_env


@dataclass
class RakutenApiClient:
    api_key: str = ""      # "serviceSecret:licenseKey" の形式
    shop_id: str = ""
    base_url: str = ""

    def __post_init__(self):
        self.api_key = self.api_key or get_env("RAKUTEN_API_KEY", "")
        self.shop_id = self.shop_id or get_env("RAKUTEN_SHOP_ID", "")
        self.base_url = self.base_url or get_env("RAKUTEN_BASE_URL", "https://api.rms.rakuten.co.jp")

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

    def _exclude_refunded_orders_enabled(self) -> bool:
        # 默认不过滤：只要是订单都写入。需要过滤时可在 .env 显式设置为 1/true。
        v = (get_env("RAKUTEN_EXCLUDE_REFUNDED") or "0").strip().lower()
        return v not in {"0", "false", "no", "off"}

    def _is_refunded_or_cancelled(self, order: dict[str, Any]) -> bool:
        """
        受注APIの返却から「キャンセル/返品/返金」系を除外するためのヒューリスティック。

        楽天側の項目名は契約/バージョンで差があるため、複数候補を確認する。
        """
        if not order:
            return False

        # 数値フラグ系（存在する場合）
        for k in ("cancelFlg", "cancelFlag", "refundFlg", "refundFlag", "returnFlg", "returnFlag"):
            try:
                if int(order.get(k) or 0) == 1:
                    return True
            except Exception:
                pass

        # ステータス文字列系
        status_raw = order.get("orderProgress") or order.get("orderStatus") or ""
        status = str(status_raw).strip()
        # 受注ステータス code 900 はキャンセル扱い（運用上の参考）
        try:
            if int(status_raw) == 900:
                return True
        except Exception:
            pass
        if status:
            keywords = [
                "キャンセル",
                "取消",
                "返品",
                "返金",
                "返送",
                "Refund",
                "Cancel",
                "Return",
            ]
            if any(x.lower() in status.lower() for x in keywords):
                return True

        # Settlement / Payment 周り（存在する場合）
        settlement = order.get("SettlementModel") or {}
        settlement_status = str(settlement.get("settlementStatus") or "").strip()
        if settlement_status and any(x in settlement_status for x in ("返金", "キャンセル", "取消")):
            return True

        return False

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

    def get_orders_by_numbers(self, order_numbers: list[str]) -> list[dict[str, Any]]:
        """Return raw Rakuten OrderModel records for the provided order numbers."""
        if not self.api_key:
            return []

        orders: list[dict[str, Any]] = []
        chunk_size = 100
        for i in range(0, len(order_numbers), chunk_size):
            chunk = [x for x in order_numbers[i:i + chunk_size] if x]
            if not chunk:
                continue
            detail_resp = self.get_order_items(chunk)
            orders.extend(detail_resp.get("OrderModelList", []) or [])
        return orders

    def update_order_shipping(
        self,
        order_number: str,
        basket_shipping_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Register tracking/shipping completion for one Rakuten order.

        basket_shipping_list shape:
        [
          {
            "basketId": 123,
            "ShippingModelList": [
              {
                "shippingNumber": "...",
                "deliveryCompany": "1003",
                "shippingDate": "2026-05-14"
              }
            ]
          }
        ]
        """
        endpoint = f"{self.base_url}/es/2.0/order/updateOrderShipping/"
        payload = {
            "orderNumber": order_number,
            "BasketidModelList": basket_shipping_list,
        }
        resp = requests.post(endpoint, headers=self._get_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

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
                if self._exclude_refunded_orders_enabled() and self._is_refunded_or_cancelled(order):
                    continue
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

    def get_orders_detailed(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """指定期間の全受注を取得し、Lark への書き込みに使う"原始字段袋"のリストを返す。

        各要素の構造（一部，APIから取れた値のみ）::

            {
                "orderNumber": "302100-...",
                "orderDatetime": "2026-04-30T10:23:11+09:00",
                "totalPrice": 19800,
                "requestPrice": 19800,
                "subtotalPrice": 18000,
                "couponAllTotalPrice": 0,
                "pointPrice": 0,
                "shippingFee": 0,
                "settlementMethod": "クレジットカード",
                "ordererName": "山田 太郎",
                "ordererPrefecture": "東京都",
                "items": [
                    {
                        "manageNumber": "PD50",
                        "itemNumber": "...",
                        "itemName": "MOFT スマホスタンド",
                        "units": 1,
                        "price": 2980,
                    },
                    ...
                ],
                "_raw": {<原始 OrderModel>},
            }
        """
        if not self.api_key:
            print("⚠️ Rakuten API キーが未設定。詳細取得はスキップ。")
            return []

        # Step 1: 受注番号一覧
        all_order_numbers: list[str] = []
        page = 1
        while True:
            result = self.search_orders(start_date, end_date, page)
            order_model_list = result.get("orderNumberList", [])
            if not order_model_list:
                break
            all_order_numbers.extend(order_model_list)
            pagination = result.get("PaginationResponseModel", {})
            total_pages = pagination.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1

        if not all_order_numbers:
            return []

        # Step 2: 受注詳細をチャンクで取得
        orders_out: list[dict[str, Any]] = []
        chunk_size = 100
        for i in range(0, len(all_order_numbers), chunk_size):
            chunk = all_order_numbers[i:i + chunk_size]
            detail_resp = self.get_order_items(chunk)

            for order in detail_resp.get("OrderModelList", []):
                if self._exclude_refunded_orders_enabled() and self._is_refunded_or_cancelled(order):
                    continue
                # 注文者
                orderer = order.get("OrdererModel") or {}
                orderer_name_parts = [
                    (orderer.get("familyName") or "").strip(),
                    (orderer.get("firstName") or "").strip(),
                ]
                orderer_name = " ".join([p for p in orderer_name_parts if p]).strip()

                settlement = order.get("SettlementModel") or {}

                # 商品アイテム
                items_out: list[dict[str, Any]] = []
                for package in order.get("PackageModelList", []) or []:
                    for item in package.get("ItemModelList", []) or []:
                        sku_models = item.get("SkuModelList") or item.get("skuModelList") or []
                        first_sku_model = sku_models[0] if sku_models and isinstance(sku_models, list) else {}
                        # 受注 API 里并不总是带「システム連携用SKU番号」，
                        # 尽量保留可能字段，供上层映射使用。
                        system_sku = (
                            item.get("merchantDefinedSkuId")
                            or item.get("merchantDefinedSkuID")
                            or first_sku_model.get("merchantDefinedSkuId")
                            or first_sku_model.get("merchantDefinedSkuID")
                            or item.get("systemSku")
                            or item.get("systemSKU")
                            or item.get("systemSkuNumber")
                            or item.get("externalSku")
                            or ""
                        )
                        items_out.append({
                            "manageNumber": item.get("manageNumber") or "",
                            "itemNumber": item.get("itemNumber") or "",
                            "itemName": item.get("itemName") or "",
                            "units": int(item.get("units") or 0),
                            "price": float(item.get("price") or 0),
                            "systemSku": system_sku,
                            "_raw_item": item,
                        })

                orders_out.append({
                    "orderNumber": order.get("orderNumber") or "",
                    "orderDatetime": order.get("orderDatetime") or "",
                    "totalPrice": float(order.get("totalPrice") or 0),
                    "requestPrice": float(order.get("requestPrice") or 0),
                    "subtotalPrice": float(order.get("subtotalPrice") or 0),
                    "couponAllTotalPrice": float(order.get("couponAllTotalPrice") or 0),
                    "pointPrice": float(order.get("pointPrice") or 0),
                    "shippingFee": float(order.get("postagePrice") or order.get("shippingFee") or 0),
                    "settlementMethod": settlement.get("settlementMethod") or "",
                    "ordererName": orderer_name,
                    "ordererPrefecture": orderer.get("prefecture") or "",
                    "ordererZipCode": (orderer.get("zipCode1") or "") + (orderer.get("zipCode2") or ""),
                    "orderStatus": order.get("orderProgress") or "",
                    "orderProgressCode": order.get("orderProgress") or "",
                    "items": items_out,
                    "_raw": order,
                })

        return orders_out
