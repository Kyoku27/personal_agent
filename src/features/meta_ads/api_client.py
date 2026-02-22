from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetaAdsApiClient:
    access_token: str
    account_id: str

    def ping(self) -> bool:
        """占位：后续实现 API 连通性检查。"""
        return all([self.access_token, self.account_id])

