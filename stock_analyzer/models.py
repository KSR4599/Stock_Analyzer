from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StockScore:
    symbol: str
    score: float
    last_price: float
    action: str
    suggested_amount: float
    setup: str = "unknown"
    risk_level: str = "unknown"
    market_score: float | None = None
    catalyst_score: float = 0.0
    catalyst_provider: str = "none"
    catalysts: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def is_alert(self) -> bool:
        return self.suggested_amount > 0
