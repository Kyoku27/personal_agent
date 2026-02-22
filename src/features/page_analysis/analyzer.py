"""
页面分析器：抓取 URL，提取 SEO 相关字段与页面结构摘要。
适用于 Amazon、Rakuten 等电商页面分析（后续可扩展平台特定规则）。
"""

from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup


@dataclass
class PageAnalysisResult:
    """单次页面分析结果"""

    url: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    h1_list: list[str] = field(default_factory=list)
    meta_keywords: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None

    def to_summary(self) -> str:
        """便于阅读的摘要文本"""
        if self.error:
            return f"[错误] {self.url}\n  {self.error}"
        lines = [
            f"URL: {self.url}",
            f"标题(title): {self.title or '(无)'}",
            f"Meta描述: {self.meta_description or '(无)'}",
            f"H1: {', '.join(self.h1_list) if self.h1_list else '(无)'}",
        ]
        if self.og_title:
            lines.append(f"OG标题: {self.og_title}")
        if self.og_description:
            lines.append(f"OG描述: {self.og_description}")
        return "\n".join(lines)


class PageAnalyzer:
    """页面分析：请求 URL，解析 HTML，提取 SEO 与结构信息。"""

    def __init__(self, timeout: int = 15, headers: Optional[dict] = None):
        self.timeout = timeout
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def analyze(self, url: str) -> PageAnalysisResult:
        """分析单个 URL，返回 SEO 与结构信息。"""
        result = PageAnalysisResult(url=url)
        try:
            resp = requests.get(
                url,
                timeout=self.timeout,
                headers=self.headers,
            )
            result.status_code = resp.status_code
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # title
            tag = soup.find("title")
            result.title = (tag.get_text(strip=True) or "").strip() or None

            # meta description
            tag = soup.find("meta", attrs={"name": "description"})
            if tag and tag.get("content"):
                result.meta_description = tag["content"].strip()

            # meta keywords
            tag = soup.find("meta", attrs={"name": "keywords"})
            if tag and tag.get("content"):
                result.meta_keywords = tag["content"].strip()

            # Open Graph
            tag = soup.find("meta", attrs={"property": "og:title"})
            if tag and tag.get("content"):
                result.og_title = tag["content"].strip()
            tag = soup.find("meta", attrs={"property": "og:description"})
            if tag and tag.get("content"):
                result.og_description = tag["content"].strip()

            # H1
            result.h1_list = [
                h.get_text(strip=True)
                for h in soup.find_all("h1")
                if h.get_text(strip=True)
            ]

        except requests.RequestException as e:
            result.error = str(e)
        except Exception as e:
            result.error = str(e)

        return result


def run_example():
    """在 personal_agent 环境中可运行：python -m agent.src.features.page_analysis.analyzer"""
    analyzer = PageAnalyzer()
    # 示例：分析一个公开页面
    result = analyzer.analyze("https://www.example.com")
    print(result.to_summary())


if __name__ == "__main__":
    run_example()
