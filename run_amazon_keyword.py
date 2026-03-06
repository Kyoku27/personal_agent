"""
Amazon 关键词自然位/広告位追踪到飞书电子表格。
在 agent 目录下运行：python run_amazon_keyword.py [--sheet KW追踪] [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.features.ecommerce.amazon.keyword_tracker import run_keyword_tracking


def main():
    parser = argparse.ArgumentParser(description="Amazon 关键词位置追踪到飞书 Sheet")
    parser.add_argument("--sheet", type=str, help="飞书 Sheet 名称，默认 FEISHU_KEYWORD_SHEET_NAME 或 KW追踪")
    parser.add_argument("--dry-run", action="store_true", help="只抓取解析，不写入飞书")
    args = parser.parse_args()

    result = run_keyword_tracking(sheet_title=args.sheet, dry_run=args.dry_run)
    if result["success"]:
        print(f"✅ {result['message']}")
        if args.dry_run and result.get("results"):
            print("\n--- Dry-run 结果 ---")
            for r in result["results"]:
                print(f"  Row {r['row']}: {r['asin']} | {r['keyword']} | {r['type']} → {r['position']}")
    else:
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
