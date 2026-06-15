from __future__ import annotations

import numpy as np
import pandas as pd

from stock_analyzer.scoring import rank_symbols, score_symbol

AS_OF = pd.Timestamp("2025-12-31")


def _history(start: float, end: float, periods: int = 260, volume: float = 1_000_000) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=periods, freq="B")
    close = np.linspace(start, end, periods)
    volumes = np.full(periods, volume)
    volumes[-1] = volume * 2
    return _frame(close, volumes, dates)


def _rapid_breakout_history(periods: int = 260, volume: float = 1_000_000) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=periods, freq="B")
    x = np.linspace(0, 1, periods)
    close = 10 * np.exp(0.2 * x + 1.6 * x**3) * (1 + 0.025 * np.sin(np.arange(periods) / 2))
    volumes = np.full(periods, volume)
    volumes[-30:] = volume * 2
    volumes[-5:] = volume * 3.5
    return _frame(close, volumes, dates)


def _frame(close: np.ndarray, volume: np.ndarray, dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.03,
            "low": close * 0.97,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def test_score_rewards_rapid_relative_breakout() -> None:
    spy = _history(100, 115)
    strong = _rapid_breakout_history()

    scored = score_symbol(
        symbol="MOON",
        history=strong,
        benchmark_history=spy,
        budget=250,
        alert_threshold=78,
        as_of=AS_OF,
    )

    assert scored.score >= 78
    assert scored.action == "candidate"
    assert scored.suggested_amount == 250
    assert scored.setup == "breakout momentum"
    assert any("relative strength" in reason for reason in scored.reasons)


def test_rank_symbols_orders_by_score() -> None:
    histories = {
        "SPY": _history(100, 115),
        "SLOW": _history(50, 52),
        "FAST": _rapid_breakout_history(),
    }

    ranked = rank_symbols(histories, budget=250, alert_threshold=78, as_of=AS_OF)

    assert ranked[0].symbol == "FAST"
    assert ranked[-1].symbol == "SLOW"


def test_low_liquidity_name_does_not_trigger_alert() -> None:
    spy = _history(100, 115)
    thin = _rapid_breakout_history(volume=10_000)

    scored = score_symbol(
        symbol="THIN",
        history=thin,
        benchmark_history=spy,
        budget=250,
        alert_threshold=78,
        as_of=AS_OF,
    )

    assert scored.action != "candidate"
    assert scored.suggested_amount == 0
    assert scored.score < 78


def test_broken_downtrend_is_capped_below_candidate() -> None:
    spy = _history(100, 115)
    weak = _history(80, 35, volume=1_000_000)

    scored = score_symbol(
        symbol="WEAK",
        history=weak,
        benchmark_history=spy,
        budget=250,
        alert_threshold=78,
        as_of=AS_OF,
    )

    assert scored.action == "skip"
    assert scored.suggested_amount == 0
    assert scored.score < 68


def test_stale_data_is_capped_below_candidate() -> None:
    spy = _history(100, 115)
    strong = _rapid_breakout_history()

    scored = score_symbol(
        symbol="STALE",
        history=strong,
        benchmark_history=spy,
        budget=250,
        alert_threshold=78,
        as_of=pd.Timestamp("2026-02-01"),
    )

    assert scored.action == "skip"
    assert scored.suggested_amount == 0
    assert scored.score <= 40
    assert any("stale" in risk for risk in scored.risks)
