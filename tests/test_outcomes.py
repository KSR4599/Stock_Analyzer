from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.database import StockDatabase
from stock_analyzer.models import StockScore
from stock_analyzer.outcomes import compute_forward_outcome, summarize_episode_calibration


def _history(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": values, "volume": [1000] * len(values)},
        index=pd.bdate_range("2026-06-01", periods=len(values)),
    )


def test_compute_forward_outcome_uses_trading_bars_and_benchmark() -> None:
    row = {
        "run_id": 1,
        "symbol": "TEST",
        "started_at": "2026-06-02T12:00:00+00:00",
        "last_price": 11.0,
    }

    outcome = compute_forward_outcome(
        row,
        history=_history([10, 11, 12, 9, 13]),
        benchmark_history=_history([100, 100, 102, 101, 103]),
        horizon_days=3,
        evaluated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert outcome is not None
    assert outcome.exit_price == 13
    assert outcome.return_pct == (13 / 11 - 1) * 100
    assert outcome.max_favorable_pct == (13 / 11 - 1) * 100
    assert outcome.max_adverse_pct == (9 / 11 - 1) * 100
    assert outcome.benchmark_return_pct == pytest.approx(3.0)


def test_compute_forward_outcome_waits_for_maturity() -> None:
    row = {
        "run_id": 1,
        "symbol": "TEST",
        "started_at": "2026-06-04T12:00:00+00:00",
        "last_price": 12.0,
    }

    assert (
        compute_forward_outcome(
            row,
            history=_history([10, 11, 12]),
            benchmark_history=None,
            horizon_days=1,
            evaluated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
        is None
    )


def test_forward_excursions_are_zero_bounded() -> None:
    row = {
        "run_id": 1,
        "symbol": "TEST",
        "started_at": "2026-06-02T12:00:00+00:00",
        "last_price": 11.0,
    }

    outcome = compute_forward_outcome(
        row,
        history=_history([10, 11, 12, 13]),
        benchmark_history=None,
        horizon_days=2,
        evaluated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert outcome is not None
    assert outcome.max_favorable_pct > 0
    assert outcome.max_adverse_pct == 0


def test_outcome_status_groups_actions(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    run_id = database.create_run(
        datetime(2026, 6, 2, tzinfo=timezone.utc),
        "yfinance",
        "manual",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    database.insert_scores(
        run_id,
        [StockScore("TEST", 90, 11, "candidate", 250)],
    )
    row = database.get_pending_outcome_scores(
        ["TEST"],
        1,
        before=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )[0]
    outcome = compute_forward_outcome(
        row,
        history=_history([10, 11, 12]),
        benchmark_history=_history([100, 100, 101]),
        horizon_days=1,
        evaluated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )
    assert outcome is not None
    database.insert_score_outcomes([outcome])

    status = database.get_outcome_status()

    assert status["outcome_count"] == 1
    assert status["summaries"][0]["action"] == "candidate"
    assert status["summaries"][0]["win_rate_pct"] == 100.0


def test_episode_calibration_collapses_repeated_scans() -> None:
    rows = [
        {
            "run_id": 1,
            "symbol": "TEST",
            "score": 80,
            "action": "candidate",
            "started_at": "2026-06-01T09:00:00+00:00",
            "horizon_days": 1,
            "return_pct": 2,
            "relative_return_pct": 1,
            "max_adverse_pct": -1,
        },
        {
            "run_id": 2,
            "symbol": "TEST",
            "score": 82,
            "action": "candidate",
            "started_at": "2026-06-01T12:00:00+00:00",
            "horizon_days": 1,
            "return_pct": 3,
            "relative_return_pct": 2,
            "max_adverse_pct": -2,
        },
        {
            "run_id": 3,
            "symbol": "TEST",
            "score": 70,
            "action": "watch",
            "started_at": "2026-06-01T15:00:00+00:00",
            "horizon_days": 1,
            "return_pct": -1,
            "relative_return_pct": -2,
            "max_adverse_pct": -3,
        },
    ]

    status = summarize_episode_calibration(rows)

    assert status["raw_observation_count"] == 3
    assert status["episode_observation_count"] == 2
    candidate = next(
        row for row in status["action_summaries"] if row["action"] == "candidate"
    )
    assert candidate["count"] == 1
    assert candidate["average_return_pct"] == 2
    candidate_band = next(
        row
        for row in status["action_score_band_summaries"]
        if row["action"] == "candidate" and row["score_band"] == "78-84.9"
    )
    assert candidate_band["count"] == 1
    assert candidate_band["median_relative_return_pct"] == 1
