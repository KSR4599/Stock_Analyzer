from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import plistlib
import sqlite3

import pytest

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.catalysts.models import FundamentalSnapshot, SignalContribution
from stock_analyzer.dashboard import DashboardStore, create_dashboard_app
from stock_analyzer.database import StockDatabase
from stock_analyzer.models import StockScore
from stock_analyzer.portfolio_models import PortfolioAssessment, PortfolioPosition
from stock_analyzer.portfolio_models import PortfolioPriceSnapshot
import stock_analyzer.dashboard as dashboard_module


def _dashboard_database(tmp_path) -> StockDatabase:
    database = StockDatabase(tmp_path / "dashboard.sqlite3")
    database.initialize()
    import_id, _ = database.create_portfolio_preview(
        "2026-06-21",
        "test-v1",
        [
            PortfolioPosition("TEST", 2, 8, "speculative"),
            PortfolioPosition("VOO", 1, 90, "thematic_etf"),
        ],
    )
    database.apply_portfolio_preview(import_id)
    first = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 21, 8, tzinfo=timezone.utc),
        100,
        False,
        110,
    )
    second = database.create_portfolio_monitor_run(
        import_id,
        datetime.now(timezone.utc),
        100,
        False,
        120,
    )
    for run_id, test_action in [(first, "HOLD"), (second, "WATCH")]:
        database.insert_portfolio_assessments(
            run_id,
            [
                PortfolioAssessment(
                    symbol="TEST",
                    action=test_action,
                    classification="speculative",
                    current_price=10,
                    current_value=20,
                    weight_pct=16.67,
                    return_from_cost_pct=25,
                    daily_return_pct=2,
                    return_5d_pct=5,
                    score=82,
                    reasons=["Stored deterministic reason."],
                    risks=["Stored deterministic risk."],
                ),
                PortfolioAssessment(
                    symbol="VOO",
                    action="HOLD",
                    classification="thematic_etf",
                    current_price=100,
                    current_value=100,
                    weight_pct=83.33,
                    return_from_cost_pct=11.11,
                    daily_return_pct=1,
                    return_5d_pct=2,
                    score=50,
                    reasons=["Broad market exposure."],
                    risks=[],
                ),
            ],
        )
    database.update_portfolio_notification_status(second, "failed", "ConnectionError")

    prior_production = database.create_run(
        datetime(2026, 6, 21, 12, tzinfo=timezone.utc),
        "yfinance",
        "test",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    database.insert_scores(
        prior_production,
        [
            StockScore(
                symbol="TEST",
                score=75,
                last_price=9,
                action="watch",
                suggested_amount=0,
                reasons=["Older reason."],
                risks=["Older risk."],
            )
        ],
    )
    database.record_catalyst_run(prior_production, "sec", False)
    production = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "test",
        1,
        market_requested=1,
        market_received=1,
        market_coverage_pct=100,
    )
    shadow = database.create_run(
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
        [
            StockScore(
                symbol="TEST",
                score=88,
                last_price=10,
                action="candidate",
                suggested_amount=250,
                setup="breakout",
                risk_level="medium",
                market_score=80,
                catalyst_score=8,
                catalyst_provider="sec",
                reasons=["Production reason."],
                risks=["Production risk."],
            ),
            StockScore(
                symbol="WMT",
                score=99,
                last_price=100,
                action="candidate",
                suggested_amount=250,
                reasons=["Excluded RSU symbol."],
            ),
        ],
    )
    database.insert_scores(
        shadow,
        [
            StockScore(
                symbol="TEST",
                score=91,
                last_price=10,
                action="candidate",
                suggested_amount=250,
                catalyst_provider="multi",
                reasons=["Shadow reason."],
                risks=["Shadow risk."],
            )
        ],
    )
    database.record_catalyst_run(production, "sec", False)
    database.record_catalyst_run(shadow, "multi", True)
    database.insert_catalyst_details(
        production,
        {
            "TEST": CatalystSignal(
                symbol="TEST",
                provider="sec",
                contributions=[
                    SignalContribution(
                        category="fundamentals_analyst",
                        score_delta=1.5,
                        confidence=0.8,
                        source="SEC XBRL",
                        summary="Revenue grew 25.0% year over year.",
                        event_id="sec-fundamental-revenue",
                    )
                ],
                fundamental_snapshot=FundamentalSnapshot(
                    symbol="TEST",
                    as_of=datetime.now(timezone.utc),
                    provider="sec",
                    metrics={
                        "revenue_growth_yoy_pct": 25.0,
                        "net_margin_pct": 12.5,
                        "free_cash_flow": 123000000.0,
                    },
                ),
            )
        },
    )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO normalized_events (
                run_id, symbol, provider, event_id, category, headline,
                source, published_at, url, relevance, sentiment
            ) VALUES (?, 'TEST', 'sec', 'event-1', 'filing', 'Filed an 8-K',
                      'SEC', ?, 'https://www.sec.gov/example', 1, NULL)
            """,
            (production, datetime.now(timezone.utc).isoformat()),
        )
        connection.execute(
            """
            INSERT INTO score_outcomes (
                run_id, symbol, horizon_days, evaluated_at, entry_price,
                exit_price, return_pct, benchmark_return_pct,
                relative_return_pct, max_favorable_pct, max_adverse_pct
            ) VALUES (?, 'TEST', 1, ?, 10, 11, 10, 2, 8, 12, -3)
            """,
            (production, datetime.now(timezone.utc).isoformat()),
        )
    return database


def test_dashboard_connections_are_query_only(tmp_path) -> None:
    database = _dashboard_database(tmp_path)
    store = DashboardStore(database.path)

    with store.connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO runs (started_at, provider, universe_source, universe_size) VALUES ('x', 'x', 'x', 0)")


def test_dashboard_api_separates_production_and_shadow(tmp_path) -> None:
    database = _dashboard_database(tmp_path)
    client = create_dashboard_app(database.path).test_client()

    production = client.get("/api/ideas?source=production").get_json()
    shadow = client.get("/api/ideas?source=shadow").get_json()

    assert production["source"] == "Production SEC"
    assert production["provider"] == "sec"
    assert production["ideas"][0]["reasons"] == ["Production reason."]
    assert production["ideas"][0]["score_delta"] == 13
    assert production["ideas"][0]["signal_state"] == "new_candidate"
    assert production["ideas"][0]["new_reasons"] == ["Production reason."]
    assert production["ideas"][0]["evidence_coverage"] > 0
    assert production["sample_count"] == 1
    assert all(item["symbol"] != "WMT" for item in production["ideas"])
    assert shadow["source"] == "Shadow Context"
    assert shadow["provider"] == "multi"
    assert shadow["ideas"][0]["reasons"] == ["Shadow reason."]

    overview = client.get("/api/overview").get_json()
    assert overview["pulse"]["new_candidates"] == 1
    assert overview["movers"][0]["symbol"] == "TEST"
    assert overview["agreement"]["sample_count"] == 1


def test_dashboard_overview_handles_new_candidate_without_score_delta(tmp_path) -> None:
    database = _dashboard_database(tmp_path)
    with database.connect() as connection:
        production_run = connection.execute(
            """
            SELECT runs.id
            FROM runs
            JOIN catalyst_runs ON catalyst_runs.run_id = runs.id
            WHERE catalyst_runs.is_shadow = 0
            ORDER BY runs.id DESC
            LIMIT 1
            """
        ).fetchone()[0]
    database.insert_scores(
        production_run,
        [
            StockScore(
                symbol="NEW",
                score=89,
                last_price=12,
                action="candidate",
                suggested_amount=250,
                setup="breakout",
                risk_level="medium",
                reasons=["Brand new production candidate."],
                risks=["Needs follow-through."],
            )
        ],
    )

    overview = create_dashboard_app(database.path).test_client().get("/api/overview")

    assert overview.status_code == 200
    changes = overview.get_json()["changes"]
    assert any(
        item["title"] == "NEW" and "score 89.0 (new)" in item["detail"]
        for item in changes
    )


def test_dashboard_routes_are_local_read_only_and_hardened(tmp_path) -> None:
    database = _dashboard_database(tmp_path)
    client = create_dashboard_app(database.path).test_client()

    response = client.get("/api/overview")
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in response.headers
    assert client.post("/api/overview").status_code == 405
    assert client.get("/api/overview", headers={"Host": "example.com"}).status_code == 403
    assert client.get("/api/stocks/WMT").status_code == 400


def test_dashboard_exposes_health_without_sensitive_material(tmp_path) -> None:
    database = _dashboard_database(tmp_path)
    client = create_dashboard_app(database.path).test_client()

    portfolio = client.get("/api/portfolio").get_json()
    health = client.get("/api/health").get_json()
    stock = client.get("/api/stocks/TEST").get_json()
    rendered = json.dumps([portfolio, health, stock])

    assert portfolio["summary"]["notification_status"] == "failed"
    assert health["shadow_promotion"]["ready_for_manual_promotion"] is False
    assert any(
        item["name"] == "twenty_scans"
        for item in health["shadow_promotion"]["criteria"]
    )
    assert any(
        item["name"] == "Portfolio PDF delivery"
        for item in health["services"]
    )
    assert stock["events"][0]["headline"] == "Filed an 8-K"
    assert stock["fundamentals"][0]["provider"] == "sec"
    assert stock["dossier"]["fundamentals"]["status"] == "available"
    assert "Market cap" in stock["dossier"]["fundamentals"]["unavailable"]
    assert stock["dossier"]["score_evidence"]["items"][0]["source"] == "SEC XBRL"
    for forbidden in [
        "TELEGRAM_BOT_TOKEN",
        "account_identifier",
        "source_pdf",
        "statement_path",
        "raw_log",
    ]:
        assert forbidden not in rendered


def test_dashboard_price_watch_is_healthy_when_idle_after_hours(
    tmp_path,
    monkeypatch,
) -> None:
    database = _dashboard_database(tmp_path)
    database.insert_portfolio_price_snapshots(
        [
            PortfolioPriceSnapshot(
                symbol="TEST",
                captured_at=datetime(2026, 6, 24, 4, tzinfo=timezone.utc),
                trade_date=date(2026, 6, 23),
                quantity=1,
                price=10,
                previous_close=9.5,
                baseline_price=9.5,
                move_pct=5.26,
                move_dollars=0.5,
                position_value=10,
                day_dollar_change=0.5,
                source="yfinance",
                freshness_seconds=60,
                degraded=False,
            )
        ]
    )
    monkeypatch.setattr(
        dashboard_module,
        "_now",
        lambda: datetime(2026, 6, 24, 8, tzinfo=timezone.utc),
    )

    health = create_dashboard_app(database.path).test_client().get("/api/health").get_json()

    price_watch = next(
        service
        for service in health["services"]
        if service["name"] == "Portfolio price watch"
    )
    assert price_watch["status"] == "healthy"
    assert "idle outside market hours" in price_watch["detail"]


def test_dashboard_launch_agent_is_local_and_isolated() -> None:
    project = Path(__file__).resolve().parents[1]
    with (
        project / "deploy/launchd/com.stock-analyzer.dashboard.template.plist"
    ).open("rb") as handle:
        dashboard = plistlib.load(handle)

    arguments = dashboard["ProgramArguments"]
    assert dashboard["Label"] == "com.stock-analyzer.dashboard"
    assert dashboard["RunAtLoad"] is True
    assert dashboard["KeepAlive"] is True
    assert dashboard["Umask"] == 0o77
    assert arguments[-3:] == ["dashboard", "--port", "8765"]
    assert "run-once" not in arguments
    assert "portfolio-run" not in arguments
