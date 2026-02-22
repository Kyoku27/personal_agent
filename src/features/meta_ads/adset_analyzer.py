from __future__ import annotations

from dataclasses import dataclass

from .api_client import MetaAdsApiClient


@dataclass
class MetaAdsetAnalyzer:
    client: MetaAdsApiClient

    def fetch_adset_metrics(self, since: str, until: str) -> list[dict]:
        """占位：后续实现按日期区间拉取指标。"""
        _ = (since, until)
        return []

