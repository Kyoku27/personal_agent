"""
任务调度占位模块。
后续可以接入 schedule/apscheduler，在此集中管理定时任务。
"""

from typing import Callable


def register_task(name: str, func: Callable[[], None]) -> None:
    """简单注册接口，占位实现。"""
    # 目前不做实际调度，仅作为将来扩展的统一入口。
    _ = (name, func)


def start_scheduler() -> None:
    """启动任务调度，占位实现。"""
    return None

