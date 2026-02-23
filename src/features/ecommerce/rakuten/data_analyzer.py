import logging
from dataclasses import dataclass
from datetime import date, timedelta

from .api_client import RakutenApiClient

logger = logging.getLogger(__name__)

@dataclass
class RakutenDataAnalyzer:
    client: RakutenApiClient

    def _month_range(self, year: int, month: int) -> tuple[date, date]:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        return start, end - timedelta(days=1)

    def get_revenue_summary(self, target_date: date) -> list[dict]:
        """按特定日期、按 SKU 汇总乐天营业额。
        
        通过 RakutenApiClient 拉取对应日期数据，并整理为飞书支持的统一结构列表。
        """
        logger.info(f"正在拉取 SKU 级别销售数据 [乐天] -> {target_date.strftime('%Y-%m-%d')}")

        # 调用 API 客户端获取原始销售单日数据 (现在返回 List)
        raw_sku_list = self.client.get_sales_data(start_date=str(target_date), end_date=str(target_date))

        results = []
        total_revenue = 0.0
        
        for item in raw_sku_list:
            # 这里的 sku 对应乐天后台下载受注清单的 sku或者商品管理番号
            sku_name = item.get("sku", "UNKNOWN-SKU")
            revenue = float(item.get("sales", 0.0))
            orders = int(item.get("order_count", 0))

            total_revenue += revenue

            results.append({
                "platform": "rakuten",
                "date": target_date,
                "sku": sku_name,
                "revenue": revenue,
                "orders": orders,
            })
            
        logger.info(f"乐天 {target_date.strftime('%Y-%m-%d')} (按SKU) 汇总完成: 共 {len(results)} 个 SKU, 总营业额={total_revenue}")
        return results

