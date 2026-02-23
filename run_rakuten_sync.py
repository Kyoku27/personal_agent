import argparse
import datetime
import logging
import sys

from src.core.config_manager import get_env
from src.features.ecommerce.rakuten.api_client import RakutenApiClient
from src.features.ecommerce.rakuten.data_analyzer import RakutenDataAnalyzer
from src.features.feishu.bot_client import FeishuBotClient
from src.features.feishu.sheet_manager import FeishuSheetManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Synchronize Rakuten sales data to Feishu horizontal pivot table.")
    parser.add_argument("--date", type=str, help="Date to sync in YYYY-MM-DD format (defaults to yesterday)")
    parser.add_argument("--inspect", action="store_true", help="Inspect and print Lark table columns instead of syncing data")
    args = parser.parse_args()

    if args.inspect:
        feishu_bot_token = get_env("FEISHU_BOT_TOKEN", "dummy_token")
        feishu_client = FeishuBotClient(bot_token=feishu_bot_token)
        sheet_manager = FeishuSheetManager(client=feishu_client)
        # Using the base/table user provided via chat
        print("🔍 开始读取飞书表结构 (需确保 .env 有效)...")
        sheet_manager.list_table_fields(app_token="XMbqbEOZxayMASsm8qQjnaXspEe", table_id="tblMX04uAXVPzalZ")
        return

    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("格式错误，请使用 YYYY-MM-DD 格式的日期。")
            sys.exit(1)
    else:
        # Default to yesterday for daily scripts
        target_date = datetime.date.today() - datetime.timedelta(days=1)

    logger.info(f"🚀 开始执行日结乐天同步(横表模式): {target_date.strftime('%Y-%m-%d')}")

    # 1. 初始化 Rakuten 客户端和分析器
    rakuten_client = RakutenApiClient()
    analyzer = RakutenDataAnalyzer(client=rakuten_client)

    # 2. 获取单日营业额数据 (现返回每日 SKU 列表)
    logger.info(f"获取乐天 API 数据中 (日期: {target_date})...")
    summary_data_list = analyzer.get_revenue_summary(target_date)
    logger.info(f"乐天日报获取成功: 返回了 {len(summary_data_list)} 个 商品(SKU) 数据")

    # 3. 写入飞书透视多维表格
    feishu_bot_token = get_env("FEISHU_BOT_TOKEN", "dummy_token")
    feishu_client = FeishuBotClient(bot_token=feishu_bot_token)
    sheet_manager = FeishuSheetManager(client=feishu_client)

    logger.info("将数据更新到飞书多维表格[横表]中...")
    success_count = 0
    try:
        for sku_data in summary_data_list:
            sheet_manager.upsert_pivot_revenue_record(
                app_token=None,  # Configured in config.yaml / .env
                table_id=None,   # Configured in config.yaml / .env
                target_date=target_date,  # 传入具体日期以确定写入 x 日的列
                data=sku_data
            )
            success_count += 1
            
        logger.info(f"✅ 成功同步到飞书横表！共更新/插入了 {success_count} 个 SKU 记录。")
    except Exception as e:
        logger.error(f"❌ 同步到飞书失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
