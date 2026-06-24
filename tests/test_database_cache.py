from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.models import NewsItem, SignalContribution
from stock_analyzer.database import StockDatabase
from stock_analyzer.models import StockScore


def test_initialize_migrates_provider_calls_with_run_id(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE provider_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                symbol TEXT,
                ok INTEGER NOT NULL,
                status TEXT NOT NULL,
                item_count INTEGER DEFAULT 0,
                cache_hit INTEGER DEFAULT 0,
                message TEXT NOT NULL
            )
            """
        )

    database = StockDatabase(path)
    database.initialize()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(provider_calls)").fetchall()
        }
    assert "run_id" in columns


def test_provider_cache_expiry_and_audit_tables(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    database.set_provider_cache("alpha_vantage", "OVERVIEW:TEST", {"ok": True}, old)

    assert database.get_provider_cache("alpha_vantage", "OVERVIEW:TEST") is not None
    assert (
        database.get_provider_cache(
            "alpha_vantage",
            "OVERVIEW:TEST",
            max_age_hours=24,
        )
        is None
    )

    database.record_provider_call(
        "alpha_vantage",
        "OVERVIEW",
        "TEST",
        True,
        "ok",
    )
    assert database.count_provider_calls_since(
        "alpha_vantage",
        datetime.now(timezone.utc) - timedelta(hours=1),
    ) == 1


def test_provider_status_summary_reports_cache_success_and_plan_limits(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    database.record_provider_call("finnhub", "news", "TEST", True, "ok")
    database.record_provider_call(
        "finnhub",
        "news",
        "TEST",
        True,
        "cache",
        cache_hit=True,
    )
    database.record_provider_call(
        "fmp",
        "stock_news",
        "TEST",
        False,
        "plan_limited",
    )

    summary = {
        item["provider"]: item for item in database.get_provider_status_summary()
    }

    assert summary["finnhub"]["call_count"] == 2
    assert summary["finnhub"]["success_rate_pct"] == 100
    assert summary["finnhub"]["cache_rate_pct"] == 50
    assert summary["fmp"]["plan_limited_count"] == 1


def test_wmt_is_removed_from_cache_payloads_and_run_failures(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    database.set_provider_cache(
        "sec",
        "ticker_map:all",
        {
            "0": {"ticker": "TEST", "title": "Test Corp"},
            "1": {"ticker": "WMT", "title": "Walmart Inc."},
        },
    )
    run_id = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "manual",
        2,
        market_failures=["TEST", "WMT"],
    )

    payload, _ = database.get_provider_cache("sec", "ticker_map:all")
    with database.connect() as connection:
        failures = connection.execute(
            "SELECT market_failures_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]

    assert payload == {"0": {"ticker": "TEST", "title": "Test Corp"}}
    assert failures == '["TEST"]'


def test_contributions_and_events_are_persisted(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    run_id = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "manual",
        1,
    )
    item = NewsItem(
        symbol="TEST",
        headline="Test announces AI partnership",
        published_at=datetime.now(timezone.utc),
        source="Example",
        url="https://example.com/story",
        relevance=1.0,
        fingerprint="story",
        cluster_id="story",
    )
    signal = aggregate_signal(
        symbol="TEST",
        provider="multi",
        contributions=[
            SignalContribution("news", 1, 0.1, "test", "story", "story")
        ],
        news_items=[item],
    )

    database.insert_catalyst_details(run_id, {"TEST": signal})

    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM score_contributions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM normalized_events").fetchone()[0] == 1


def test_shadow_status_tracks_metrics_and_reviews(tmp_path) -> None:
    database = StockDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    first_run = database.create_run(started, "yfinance", "manual", 1)
    second_run = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "manual",
        1,
    )
    empty_run = database.create_run(
        datetime.now(timezone.utc),
        "yfinance",
        "manual",
        1,
        market_degraded=True,
    )
    watch = StockScore("TEST", 75, 10, "watch", 0)
    candidate = StockScore("TEST", 82, 10, "candidate", 250)
    database.insert_scores(first_run, [watch])
    database.insert_scores(second_run, [candidate])
    database.insert_scores(empty_run, [candidate])
    database.record_catalyst_run(first_run, "multi", True)
    database.record_catalyst_run(second_run, "multi", True)
    database.record_catalyst_run(empty_run, "multi", True)
    signal = aggregate_signal(
        "TEST",
        "multi",
        [SignalContribution("news", 2, 0.1, "test", "story", "story")],
    )
    database.insert_catalyst_details(second_run, {"TEST": signal})
    database.record_provider_call("sec", "submissions", "TEST", False, "error")
    database.begin_provider_call_capture()
    database.record_provider_call("test", "news", "TEST", True, "ok")
    call_ids = database.finish_provider_call_capture()
    database.attach_provider_calls_to_run(second_run, call_ids)

    status = database.get_shadow_status(days=7)

    assert status["scan_count"] == 2
    assert status["remote_call_count"] == 1
    assert status["provider_success_rate_pct"] == 100.0
    assert status["promotion_gate"]["ready_for_manual_promotion"] is False
    assert any(
        item["name"] == "twenty_scans" and item["passed"] is False
        for item in status["promotion_gate"]["criteria"]
    )
    assert status["provider_summaries"] == [
        {
            "provider": "test",
            "remote_call_count": 1,
            "success_rate_pct": 100.0,
            "plan_limited_count": 0,
            "activation_state": "access_reliable",
        }
    ]
    assert status["candidate_state_changes"] == 1
    assert status["unreviewed_candidate_changes"] == [(second_run, "TEST")]

    database.mark_shadow_review(second_run, "TEST", "approved", "reviewed")
    reviewed = database.get_shadow_status(days=7)

    assert reviewed["unreviewed_candidate_changes"] == []

    market_health = database.get_market_health_status(days=7)
    assert market_health["scan_count"] == 0
