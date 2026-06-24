from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from stock_analyzer.catalysts.models import (
    FundamentalSnapshot,
    MarketContext,
    NewsItem,
    SignalContribution,
)


@dataclass(frozen=True)
class CatalystSignal:
    symbol: str
    score_delta: float = 0.0
    confidence: float = 0.0
    provider: str = "none"
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    contributions: list[SignalContribution] = field(default_factory=list)
    news_items: list[NewsItem] = field(default_factory=list)
    fundamental_snapshot: FundamentalSnapshot | None = None
    market_context: MarketContext | None = None


class CatalystProvider(ABC):
    name: str

    @abstractmethod
    def fetch_signals(self, symbols: list[str], run_at: datetime) -> dict[str, CatalystSignal]:
        """Return catalyst signals keyed by symbol."""


class NullCatalystProvider(CatalystProvider):
    name = "none"

    def __init__(self, reason: str = "Catalyst enrichment disabled.") -> None:
        self.reason = reason

    def fetch_signals(self, symbols: list[str], run_at: datetime) -> dict[str, CatalystSignal]:
        return {
            symbol: CatalystSignal(
                symbol=symbol,
                provider=self.name,
                reasons=[self.reason],
            )
            for symbol in symbols
        }
