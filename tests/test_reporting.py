from __future__ import annotations

from datetime import datetime

from stock_analyzer.models import StockScore
from stock_analyzer.reporting import format_error_alert, format_report


RUN_AT = datetime(2026, 6, 15, 9, 30)


def test_candidate_alert_report_is_labeled_and_budgeted() -> None:
    report = format_report(
        scores=[
            StockScore(
                symbol="MOON",
                score=90,
                last_price=10,
                action="candidate",
                suggested_amount=250,
                metrics={
                    "signal_state": "new_candidate",
                    "score_delta": 8,
                    "rank_delta": 4,
                    "new_reasons": ["strong breakout"],
                },
                reasons=["strong breakout"],
                risks=["high volatility"],
            )
        ],
        run_at=RUN_AT,
        provider="yfinance",
        catalyst_provider="sec",
        catalyst_top_n=1,
        universe_source="manual",
        universe_size=1,
        budget=250,
        threshold=78,
        top_n=5,
        report_kind="candidate_alert",
    )

    assert report.startswith("Stock Analyzer candidate alert")
    assert "Trigger: $250 candidate" in report
    assert "ALERTS: 1 candidate" in report
    assert "Change: new candidate | score +8.0 | rank +4 | 1 new insight" in report


def test_error_alert_omits_exception_details() -> None:
    error = RuntimeError("token=secret should not be echoed")

    report = format_error_alert(error=error, run_at=RUN_AT)

    assert report.startswith("Stock Analyzer error alert")
    assert "RuntimeError" in report
    assert "token=secret should not be echoed" not in report


def test_degraded_market_report_suppresses_candidates_visibly() -> None:
    report = format_report(
        scores=[],
        run_at=RUN_AT,
        provider="yfinance",
        catalyst_provider="sec",
        catalyst_top_n=0,
        universe_source="manual",
        universe_size=5,
        budget=250,
        threshold=78,
        top_n=5,
        market_requested=5,
        market_received=3,
        market_coverage_pct=60,
        market_degraded=True,
        market_failures=["MU", "ARM"],
    )

    assert "Market data: 3/5 (60.0%) | DEGRADED" in report
    assert "Candidate alerts were suppressed" in report
    assert "MU, ARM" in report
