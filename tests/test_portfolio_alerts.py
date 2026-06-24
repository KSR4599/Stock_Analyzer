from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from stock_analyzer.database import StockDatabase
from stock_analyzer.portfolio_alerts import (
    build_eod_report,
    build_price_snapshots,
    detect_price_alerts,
    group_alert_messages,
)
from stock_analyzer.portfolio_models import PortfolioPosition


RUN_AT = datetime.fromisoformat("2026-06-23T10:00:00-07:00")


def _history(values: list[float], start: str = "2026-06-20") -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values, "Volume": [1000] * len(values)},
        index=pd.date_range(start, periods=len(values), freq="D"),
    )


def _intraday(values: list[float], start: str = "2026-06-23 09:55") -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values, "Volume": [1000] * len(values)},
        index=pd.date_range(start, periods=len(values), freq="5min"),
    )


def test_detects_5_10_15_percent_moves_and_formats_one_message() -> None:
    positions = {"TEST": PortfolioPosition("TEST", 10, 9)}
    snapshots = build_price_snapshots(
        positions,
        {"TEST": _intraday([116])},
        {"TEST": _history([100, 116], "2026-06-22")},
        RUN_AT,
        source="yfinance",
    )

    alerts = detect_price_alerts(snapshots, set(), RUN_AT)
    messages = group_alert_messages(snapshots, alerts, RUN_AT)

    assert [alert.threshold_pct for alert in alerts] == [5.0, 10.0, 15.0]
    assert len(messages) == 1
    assert "crossing 5%, 10%, 15%" in messages[0][2]
    assert "Research only" in messages[0][2]


def test_dedupes_already_sent_thresholds_but_keeps_new_higher_level() -> None:
    positions = {"TEST": PortfolioPosition("TEST", 10, 9)}
    snapshots = build_price_snapshots(
        positions,
        {"TEST": _intraday([112])},
        {"TEST": _history([100, 112], "2026-06-22")},
        RUN_AT,
        source="yfinance",
    )

    alerts = detect_price_alerts(
        snapshots,
        {("TEST", "up", 5.0)},
        RUN_AT,
    )

    assert [alert.threshold_pct for alert in alerts] == [10.0]


def test_database_alert_dedupes_per_trade_date_and_resets_daily(tmp_path) -> None:
    database = StockDatabase(tmp_path / "alerts.sqlite3")
    database.initialize()
    alert = detect_price_alerts(
        build_price_snapshots(
            {"TEST": PortfolioPosition("TEST", 1, 10)},
            {"TEST": _intraday([106])},
            {"TEST": _history([100, 106], "2026-06-22")},
            RUN_AT,
            source="yfinance",
        ),
        set(),
        RUN_AT,
    )[0]

    assert database.insert_portfolio_price_alert(alert, "delivered")
    assert ("TEST", "up", 5.0) in database.get_sent_portfolio_price_alert_levels(
        date(2026, 6, 23)
    )
    assert not database.insert_portfolio_price_alert(alert, "delivered")
    assert database.get_sent_portfolio_price_alert_levels(date(2026, 6, 24)) == set()


def test_wmt_is_not_stored_or_alerted(tmp_path) -> None:
    database = StockDatabase(tmp_path / "alerts.sqlite3")
    database.initialize()
    snapshots = build_price_snapshots(
        {
            "TEST": PortfolioPosition("TEST", 1, 10),
            "WMT": PortfolioPosition("WMT", 1, 10),
        },
        {
            "TEST": _intraday([106]),
            "WMT": _intraday([106]),
        },
        {
            "TEST": _history([100, 106], "2026-06-22"),
            "WMT": _history([100, 106], "2026-06-22"),
        },
        RUN_AT,
        source="yfinance",
    )
    database.insert_portfolio_price_snapshots(snapshots)
    alerts = detect_price_alerts(snapshots, set(), RUN_AT)

    with database.connect() as connection:
        symbols = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT symbol FROM portfolio_price_snapshots"
            )
        ]
    assert symbols == ["TEST"]
    assert {alert.symbol for alert in alerts} == {"TEST"}


def test_split_or_adjustment_like_price_jump_is_degraded() -> None:
    snapshots = build_price_snapshots(
        {"TEST": PortfolioPosition("TEST", 1, 10)},
        {"TEST": _intraday([500])},
        {"TEST": _history([100, 500], "2026-06-22")},
        RUN_AT,
        source="yfinance",
    )

    assert snapshots[0].degraded
    assert detect_price_alerts(snapshots, set(), RUN_AT) == []


def test_eod_report_math_counts_gainers_losers_and_net_result() -> None:
    snapshots = build_price_snapshots(
        {
            "UP": PortfolioPosition("UP", 10, 8),
            "DOWN": PortfolioPosition("DOWN", 5, 12),
        },
        {
            "UP": _intraday([110]),
            "DOWN": _intraday([90]),
        },
        {
            "UP": _history([100, 110], "2026-06-22"),
            "DOWN": _history([100, 90], "2026-06-22"),
        },
        RUN_AT,
        source="yfinance",
    )

    report = build_eod_report(snapshots, RUN_AT, source="yfinance")

    assert report.total_value == 1550
    assert report.total_gain_dollars == 100
    assert report.total_loss_dollars == -50
    assert report.net_change_dollars == 50
    assert report.winner_count == 1
    assert report.loser_count == 1


def test_stale_intraday_bar_is_degraded_and_not_alerted() -> None:
    snapshots = build_price_snapshots(
        {"TEST": PortfolioPosition("TEST", 1, 10)},
        {"TEST": _intraday([106], "2026-06-23 07:00")},
        {"TEST": _history([100, 106], "2026-06-22")},
        RUN_AT,
        source="yfinance",
    )

    assert snapshots[0].degraded
    assert snapshots[0].message == "price bar is stale for the current session"
    assert detect_price_alerts(snapshots, set(), RUN_AT) == []
