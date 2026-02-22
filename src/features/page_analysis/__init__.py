"""
第一个功能：页面分析
用于电商页面（Amazon / Rakuten 等）的页面结构、SEO 标题与 meta 信息提取。
"""

from .analyzer import PageAnalyzer, PageAnalysisResult

__all__ = ["PageAnalyzer", "PageAnalysisResult"]
