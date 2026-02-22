from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RakutenApiClient:
    api_key: str

    def ping(self) -> bool:
        """占位：后续实现 Rakuten API 连通性检查。"""
        return bool(self.api_key)

