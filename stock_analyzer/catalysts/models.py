from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SignalContribution:
    category: str
    score_delta: float
    confidence: float
    source: str
    summary: str
    event_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    headline: str
    published_at: datetime | None
    source: str
    url: str = ""
    related_symbols: list[str] = field(default_factory=list)
    sentiment: float | None = None
    relevance: float = 0.0
    category: str = "other"
    fingerprint: str = ""
    cluster_id: str = ""


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    as_of: datetime
    provider: str
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketContext:
    as_of: datetime
    provider: str
    regime: str = "neutral"
    metrics: dict[str, object] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
