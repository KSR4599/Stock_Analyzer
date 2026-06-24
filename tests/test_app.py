from __future__ import annotations

from argparse import Namespace

import pytest

from stock_analyzer.app import (
    _parse_symbols_arg,
    _suppress_candidate_alerts,
    _with_benchmark,
    settings_from_args,
    validate_catalyst_delivery_mode,
)
from stock_analyzer.config import Settings
from stock_analyzer.models import StockScore


def test_parse_symbols_arg_normalizes_and_dedupes() -> None:
    assert _parse_symbols_arg(" arm, MRVL,arm, , souN ") == ["ARM", "MRVL", "SOUN"]


def test_with_benchmark_adds_spy_once() -> None:
    assert _with_benchmark(["ARM", "SPY"]) == ["ARM", "SPY"]
    assert _with_benchmark(["ARM"]) == ["ARM", "SPY"]


def test_settings_from_args_accepts_catalyst_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        dry_run=False,
        live=False,
        max_symbols=None,
        symbols=None,
        top_n=None,
        threshold=None,
        budget=None,
        db_path=None,
        no_catalysts=False,
        catalyst_provider="finnhub",
        catalyst_top_n=5,
        timeout=None,
    )

    settings = settings_from_args(args)

    assert settings.catalyst_provider == "finnhub"
    assert settings.catalyst_top_n == 5


def test_shadow_provider_cannot_send_live() -> None:
    settings = Settings(catalyst_provider="multi", dry_run=False)

    with pytest.raises(ValueError, match="shadow-only"):
        validate_catalyst_delivery_mode(settings)


def test_degraded_market_data_suppresses_candidate_alerts() -> None:
    result = _suppress_candidate_alerts(
        [
            StockScore(
                symbol="TEST",
                score=90,
                last_price=10,
                action="candidate",
                suggested_amount=250,
            )
        ],
        alert_threshold=78,
    )

    assert result[0].action == "watch"
    assert result[0].suggested_amount == 0
    assert "suppressed" in result[0].risks[0]
