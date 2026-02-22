from __future__ import annotations

from dataclasses import dataclass

from .api_client import MetaAdsApiClient


@dataclass
class MetaAdsExecutor:
    client: MetaAdsApiClient

    def apply_actions(self, actions: list[dict]) -> None:
        """占位：后续实现确认后的变更执行。"""
        _ = actions

