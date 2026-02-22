"""
在项目根目录运行（请先激活 conda 环境 personal_agent）：

  conda activate personal_agent
  pip install -r requirements.txt
  python run_page_analysis.py [URL]

示例：
  python run_page_analysis.py https://www.example.com
"""

import sys
from pathlib import Path

# 保证从 agent 目录运行时能正确导入 src
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.features.page_analysis import PageAnalyzer

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.example.com"
    analyzer = PageAnalyzer()
    result = analyzer.analyze(url)
    print(result.to_summary())

if __name__ == "__main__":
    main()
