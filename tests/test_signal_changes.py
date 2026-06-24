from __future__ import annotations

from datetime import datetime, timezone

from stock_analyzer.database import StockDatabase
from stock_analyzer.models import StockScore
from stock_analyzer.outcomes import ForwardOutcome


def test_score_change_annotation_tracks_rank_candidate_and_new_evidence(tmp_path) -> None:
    database = StockDatabase(tmp_path / "changes.sqlite3")
    database.initialize()
    prior = database.create_run(
        datetime(2026, 6, 22, 8, tzinfo=timezone.utc),
        "yfinance",
        "test",
        2,
        market_requested=2,
        market_received=2,
        market_coverage_pct=100,
    )
    database.insert_scores(
        prior,
        [
            StockScore("AAA", 82, 10, "candidate", 250, reasons=["Old A"], risks=["Risk A"]),
            StockScore("BBB", 70, 10, "watch", 0, reasons=["Old B"], risks=["Risk B"]),
        ],
    )
    database.record_catalyst_run(prior, "sec", False)

    annotated = database.annotate_score_changes(
        [
            StockScore("BBB", 86, 12, "candidate", 250, reasons=["Old B", "Fresh B"], risks=[]),
            StockScore("AAA", 72, 9, "watch", 0, reasons=["Old A"], risks=["Risk A", "Fresh risk"]),
        ],
        is_shadow=False,
    )

    bbb, aaa = annotated
    assert bbb.metrics["signal_state"] == "new_candidate"
    assert bbb.metrics["score_delta"] == 16
    assert bbb.metrics["rank_delta"] == 1
    assert bbb.metrics["new_reasons"] == ["Fresh B"]
    assert bbb.metrics["resolved_risks"] == ["Risk B"]
    assert aaa.metrics["signal_state"] == "lost_candidate"
    assert aaa.metrics["score_delta"] == -10
    assert aaa.metrics["new_risks"] == ["Fresh risk"]


def test_score_change_annotation_keeps_shadow_comparison_isolated(tmp_path) -> None:
    database = StockDatabase(tmp_path / "isolated.sqlite3")
    database.initialize()
    production = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "test",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    database.insert_scores(
        production,
        [StockScore("AAA", 90, 10, "candidate", 250)],
    )
    database.record_catalyst_run(production, "sec", False)

    annotated = database.annotate_score_changes(
        [StockScore("AAA", 50, 10, "skip", 0)],
        is_shadow=True,
    )

    assert annotated[0].metrics["signal_state"] == "new_coverage"
    assert "score_delta" not in annotated[0].metrics


def test_calibration_annotation_adds_measured_context_by_action_and_band(tmp_path) -> None:
    database = StockDatabase(tmp_path / "calibration.sqlite3")
    database.initialize()
    run_at = datetime(2026, 6, 20, 8, tzinfo=timezone.utc)
    run_id = database.create_run(
        run_at,
        "yfinance",
        "test",
        2,
        market_requested=2,
        market_received=2,
        market_coverage_pct=100,
    )
    database.insert_scores(
        run_id,
        [
            StockScore("AAA", 80, 10, "candidate", 250),
            StockScore("BBB", 82, 10, "candidate", 250),
        ],
    )
    database.insert_score_outcomes(
        [
            ForwardOutcome(run_id, "AAA", 3, run_at, 10, 11, 10, 2, 8, 12, -1),
            ForwardOutcome(run_id, "BBB", 3, run_at, 10, 9, -10, 2, -12, 2, -11),
        ]
    )

    annotated = database.annotate_calibration_context(
        [StockScore("CCC", 81, 10, "candidate", 250)]
    )[0]

    assert annotated.metrics["calibration_horizon_days"] == 3
    assert annotated.metrics["calibration_score_band"] == "78-84.9"
    assert annotated.metrics["calibration_sample_count"] == 2
    assert annotated.metrics["calibration_sample_type"] == "episode_adjusted"
    assert annotated.metrics["calibration_confidence"] == "thin"
    assert annotated.metrics["calibration_win_rate_pct"] == 50.0
    assert annotated.metrics["calibration_median_return_pct"] == 0.0


def test_calibration_annotation_collapses_repeated_symbol_episodes(tmp_path) -> None:
    database = StockDatabase(tmp_path / "calibration-episodes.sqlite3")
    database.initialize()
    first_at = datetime(2026, 6, 20, 8, tzinfo=timezone.utc)
    second_at = datetime(2026, 6, 20, 10, tzinfo=timezone.utc)
    first_run = database.create_run(
        first_at,
        "yfinance",
        "test",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    second_run = database.create_run(
        second_at,
        "yfinance",
        "test",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    database.insert_scores(
        first_run,
        [StockScore("AAA", 80, 10, "candidate", 250)],
    )
    database.insert_scores(
        second_run,
        [StockScore("AAA", 82, 10, "candidate", 250)],
    )
    database.insert_score_outcomes(
        [
            ForwardOutcome(first_run, "AAA", 3, first_at, 10, 11, 10, 2, 8, 12, -1),
            ForwardOutcome(second_run, "AAA", 3, second_at, 10, 8, -20, 2, -22, 1, -21),
        ]
    )

    annotated = database.annotate_calibration_context(
        [StockScore("CCC", 81, 10, "candidate", 250)]
    )[0]

    assert annotated.metrics["calibration_sample_count"] == 1
    assert annotated.metrics["calibration_win_rate_pct"] == 100.0
    assert annotated.metrics["calibration_median_return_pct"] == 10.0
