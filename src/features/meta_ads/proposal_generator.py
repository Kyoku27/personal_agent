from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetaOptimizationProposal:
    summary: str
    actions: list[dict]


class MetaProposalGenerator:
    def generate(self, metrics: list[dict]) -> MetaOptimizationProposal:
        """占位：后续根据指标生成优化提案。"""
        _ = metrics
        return MetaOptimizationProposal(summary="(placeholder)", actions=[])

