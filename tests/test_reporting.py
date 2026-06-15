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


def test_error_alert_omits_exception_details() -> None:
    error = RuntimeError("token=secret should not be echoed")

    report = format_error_alert(error=error, run_at=RUN_AT)

    assert report.startswith("Stock Analyzer error alert")
    assert "RuntimeError" in report
    assert "token=secret should not be echoed" not in report
