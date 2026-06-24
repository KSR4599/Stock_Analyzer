from __future__ import annotations

from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from stock_analyzer.models import StockScore
from stock_analyzer.pdf_reports import (
    build_portfolio_eod_pdf,
    build_portfolio_alert_pdf,
    build_universe_alert_pdf,
    portfolio_pdf_caption,
    portfolio_pdf_filename,
    universe_pdf_caption,
    universe_pdf_filename,
)
from stock_analyzer.portfolio_models import (
    PortfolioAssessment,
    PortfolioEodReport,
    PortfolioPosition,
    PortfolioPriceSnapshot,
)


RUN_AT = datetime.fromisoformat("2026-06-22T12:41:08-07:00")


def _portfolio(count: int = 22):
    positions = {}
    assessments = []
    actions = ["EXIT REVIEW", "TRIM REVIEW", "WATCH", "HOLD"]
    for index in range(count):
        symbol = f"T{index:02d}"
        positions[symbol] = PortfolioPosition(symbol, index + 1, 8 + index)
        assessments.append(
            PortfolioAssessment(
                symbol=symbol,
                action=actions[index % len(actions)],
                classification="speculative",
                current_price=10 + index,
                current_value=(index + 1) * (10 + index),
                weight_pct=100 / count,
                return_from_cost_pct=5 + index,
                daily_return_pct=index / 10,
                return_5d_pct=index / 5,
                score=70 + index,
                reasons=["Deterministic reason."],
                risks=["Deterministic risk."],
            )
        )
    return positions, assessments


def _text(pdf_bytes: bytes) -> tuple[PdfReader, str]:
    reader = PdfReader(BytesIO(pdf_bytes))
    return reader, "\n".join(page.extract_text() or "" for page in reader.pages)


def test_portfolio_pdf_has_title_timestamp_totals_and_paginated_table() -> None:
    positions, assessments = _portfolio()

    pdf = build_portfolio_alert_pdf(
        RUN_AT,
        positions,
        assessments,
        100,
        False,
        {"T00": "WATCH"},
    )
    reader, text = _text(pdf)

    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert "Portfolio Alert" in text
    assert "Monday, June 22, 2026 - 12:41 PM" in text
    assert "Complete portfolio" in text
    assert "T00" in text and "T21" in text
    assert "WMT" not in text


def test_universe_pdf_has_production_context_candidates_and_degraded_warning() -> None:
    scores = [
        StockScore(
            symbol="ARM",
            score=94,
            last_price=180,
            action="candidate",
            suggested_amount=250,
            setup="breakout",
            risk_level="medium",
            market_score=90,
            catalyst_score=4,
            catalyst_provider="sec",
            metrics={
                "signal_state": "upgraded",
                "score_delta": 6,
                "rank_delta": 3,
            },
            catalysts=["Fresh 8-K agreement."],
            reasons=["Momentum and relative strength are positive."],
            risks=["High volatility."],
        ),
        StockScore(
            symbol="TEST",
            score=70,
            last_price=20,
            action="watch",
            suggested_amount=0,
        ),
    ]

    pdf = build_universe_alert_pdf(
        scores,
        RUN_AT,
        "yfinance",
        "sec",
        "sp500",
        500,
        250,
        78,
        500,
        450,
        90,
        True,
        ["MISSING"],
        10,
    )
    reader, text = _text(pdf)

    assert len(reader.pages) >= 1
    assert "Universe Alert" in text
    assert "ARM" in text
    assert "upgraded | score +6.0 | rank +3" in text
    assert "DEGRADED MARKET DATA" in text
    assert "Production SEC evidence only" in text
    assert "WMT" not in text


def test_pdf_names_and_captions_are_short_and_timestamped() -> None:
    positions, assessments = _portfolio(3)
    scores = [
        StockScore("ARM", 90, 10, "candidate", 250),
    ]

    assert portfolio_pdf_filename(RUN_AT) == (
        "portfolio-alert-2026-06-22-1241-UTC-07-00.pdf"
    )
    assert universe_pdf_filename(RUN_AT) == (
        "universe-alert-2026-06-22-1241-UTC-07-00.pdf"
    )
    portfolio_caption = portfolio_pdf_caption(assessments, RUN_AT)
    universe_caption = universe_pdf_caption(scores, RUN_AT)

    assert len(portfolio_caption) < 200
    assert len(universe_caption) < 200
    assert "WMT" not in portfolio_caption + universe_caption


def test_eod_pdf_has_title_key_figures_tables_and_degraded_warning() -> None:
    snapshots = [
        PortfolioPriceSnapshot(
            symbol="UP",
            captured_at=RUN_AT,
            trade_date=RUN_AT.date(),
            quantity=10,
            price=110,
            previous_close=100,
            baseline_price=100,
            move_pct=10,
            move_dollars=10,
            position_value=1100,
            day_dollar_change=100,
            source="yfinance",
            freshness_seconds=60,
            degraded=False,
        ),
        PortfolioPriceSnapshot(
            symbol="DOWN",
            captured_at=RUN_AT,
            trade_date=RUN_AT.date(),
            quantity=5,
            price=90,
            previous_close=100,
            baseline_price=100,
            move_pct=-10,
            move_dollars=-10,
            position_value=450,
            day_dollar_change=-50,
            source="yfinance",
            freshness_seconds=60,
            degraded=False,
        ),
        PortfolioPriceSnapshot(
            symbol="BAD",
            captured_at=RUN_AT,
            trade_date=RUN_AT.date(),
            quantity=1,
            price=0,
            previous_close=0,
            baseline_price=0,
            move_pct=0,
            move_dollars=0,
            position_value=0,
            day_dollar_change=0,
            source="yfinance",
            freshness_seconds=None,
            degraded=True,
            message="current price unavailable",
        ),
    ]
    report = PortfolioEodReport(
        trade_date=RUN_AT.date(),
        run_at=RUN_AT,
        total_value=1550,
        start_value=1500,
        total_gain_dollars=100,
        total_loss_dollars=-50,
        net_change_dollars=50,
        net_change_pct=3.33,
        winner_count=1,
        loser_count=1,
        flat_count=0,
        source="yfinance",
        market_coverage_pct=66.67,
        degraded=True,
        snapshots=snapshots,
    )

    pdf = build_portfolio_eod_pdf(report)
    reader, text = _text(pdf)

    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert "End-of-Day Portfolio Report" in text
    assert "NET DAY" in text
    assert "DEGRADED PRICE DATA" in text
    assert "Top movers" in text
    assert "Complete portfolio day table" in text
    assert "UP" in text and "DOWN" in text
    assert "WMT" not in text
