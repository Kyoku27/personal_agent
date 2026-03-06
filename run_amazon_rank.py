"""
Amazon 日本站排名同步到飞书电子表格。
在 agent 目录下运行：python run_amazon_rank.py [--sheet 3月]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.features.ecommerce.amazon.rank_sync import run_amazon_rank_sync


def main():
    parser = argparse.ArgumentParser(description="Amazon 排名同步到飞书 Sheet")
    parser.add_argument("--sheet", type=str, help="飞书 Sheet 名称，默认用 FEISHU_SHEET_NAME 或当前月份如 3月")
    args = parser.parse_args()

    result = run_amazon_rank_sync(sheet_title=args.sheet)
    if result["success"]:
        print(f"✅ {result['message']}")
    else:
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
