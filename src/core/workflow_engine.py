"""
工作流引擎占位模块。
用于按步骤串联页面分析、电商后台、广告、飞书同步等任务。
"""

from collections.abc import Callable
from typing import Any


def run_steps(steps: list[Callable[[], Any]]) -> list[Any]:
    """按顺序依次执行给定步骤，返回每步结果。"""
    results: list[Any] = []
    for step in steps:
        results.append(step())
    return results

