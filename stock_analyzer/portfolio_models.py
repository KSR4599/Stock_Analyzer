from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


PORTFOLIO_ACTIONS = {
    "hold": "HOLD",
    "watch": "WATCH",
    "buy_more_review": "BUY-MORE REVIEW",
    "trim_review": "TRIM REVIEW",
    "exit_review": "EXIT REVIEW",
}

@dataclass(frozen=True)
class PortfolioPosition:
    symbol: str
    quantity: float
    average_cost: float
    classification: str = "adaptive"


@dataclass(frozen=True)
class PortfolioParseResult:
    statement_date: date
    positions: list[PortfolioPosition]


@dataclass(frozen=True)
class PortfolioDiff:
    added: list[PortfolioPosition] = field(default_factory=list)
    removed: list[PortfolioPosition] = field(default_factory=list)
    changed: list[tuple[PortfolioPosition, PortfolioPosition]] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class PortfolioPolicy:
    symbol: str
    classification_override: str | None
    concentration_exempt: bool
    buy_more_enabled: bool


@dataclass(frozen=True)
class PortfolioAssessment:
    symbol: str
    action: str
    classification: str
    current_price: float
    current_value: float
    weight_pct: float
    return_from_cost_pct: float
    daily_return_pct: float | None
    return_5d_pct: float | None
    score: float
    reasons: list[str]
    risks: list[str]


@dataclass(frozen=True)
class PortfolioMonitorResult:
    run_id: int
    started_at: datetime
    import_id: int
    degraded: bool
    market_coverage_pct: float
    total_invested_value: float
    assessments: list[PortfolioAssessment]


@dataclass(frozen=True)
class PortfolioPriceSnapshot:
    symbol: str
    captured_at: datetime
    trade_date: date
    quantity: float
    price: float
    previous_close: float
    baseline_price: float
    move_pct: float
    move_dollars: float
    position_value: float
    day_dollar_change: float
    source: str
    freshness_seconds: int | None
    degraded: bool
    message: str = ""


@dataclass(frozen=True)
class PortfolioPriceAlert:
    symbol: str
    trade_date: date
    direction: str
    threshold_pct: float
    triggered_at: datetime
    baseline_price: float
    current_price: float
    move_pct: float
    move_dollars: float


@dataclass(frozen=True)
class PortfolioEodReport:
    trade_date: date
    run_at: datetime
    total_value: float
    start_value: float
    total_gain_dollars: float
    total_loss_dollars: float
    net_change_dollars: float
    net_change_pct: float
    winner_count: int
    loser_count: int
    flat_count: int
    source: str
    market_coverage_pct: float
    degraded: bool
    snapshots: list[PortfolioPriceSnapshot]
