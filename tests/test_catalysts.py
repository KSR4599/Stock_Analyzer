from __future__ import annotations

from datetime import datetime

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.catalysts.fmp_provider import FmpApiError, FmpCatalystProvider, build_fmp_signal, run_fmp_smoke_test
from stock_analyzer.catalysts.scoring import apply_catalyst_signals
from stock_analyzer.models import StockScore


RUN_AT = datetime(2026, 6, 15, 12, 0, 0)


def test_fmp_signal_boosts_positive_ai_news() -> None:
    signal = build_fmp_signal(
        symbol="MOON",
        run_at=RUN_AT,
        news=[
            {
                "publishedDate": "2026-06-15T10:00:00",
                "title": "MOON announces AI data center chip partnership with major cloud customer",
            }
        ],
        earnings=[],
        grades=[],
        price_targets=[],
    )

    assert signal.score_delta > 5
    assert any("Theme heat" in reason for reason in signal.reasons)
    assert signal.events


def test_fmp_signal_penalizes_negative_news() -> None:
    signal = build_fmp_signal(
        symbol="RISK",
        run_at=RUN_AT,
        news=[
            {
                "publishedDate": "2026-06-15T10:00:00",
                "title": "RISK shares fall after downgrade, SEC probe, and dilutive offering",
            }
        ],
        earnings=[],
        grades=[],
        price_targets=[],
    )

    assert signal.score_delta < 0
    assert signal.risks


def test_fmp_http_error_does_not_leak_api_key(monkeypatch) -> None:
    class Response:
        status_code = 401
        ok = False

    def fake_get(*args, **kwargs):
        return Response()

    monkeypatch.setattr("stock_analyzer.catalysts.fmp_provider.requests.get", fake_get)
    provider = FmpCatalystProvider(api_key="secret-key")

    try:
        provider._get("profile", {"symbol": "AAPL"})
    except FmpApiError as exc:
        assert "secret-key" not in str(exc)
        assert "authorization failed" in str(exc)
    else:
        raise AssertionError("expected FmpApiError")


def test_fmp_smoke_test_reports_endpoint_counts(monkeypatch) -> None:
    def fake_get(self, endpoint, params):
        if endpoint == "price-target-summary":
            return {"symbol": params["symbol"]}
        return [{"symbol": params.get("symbol") or params.get("symbols")}]

    monkeypatch.setattr(FmpCatalystProvider, "_get", fake_get)

    checks = run_fmp_smoke_test(api_key="secret-key", symbol="NVDA")

    assert len(checks) == 5
    assert all(check.ok for check in checks)
    assert {check.name for check in checks} == {
        "profile",
        "stock_news",
        "earnings",
        "analyst_grades",
        "price_target_summary",
    }


def test_catalyst_can_upgrade_watch_but_not_skip() -> None:
    watch = StockScore(
        symbol="WATCH",
        score=73,
        market_score=73,
        last_price=10,
        action="watch",
        suggested_amount=0,
        risk_level="medium",
    )
    skip = StockScore(
        symbol="SKIP",
        score=55,
        market_score=55,
        last_price=10,
        action="skip",
        suggested_amount=0,
        risk_level="low",
    )
    signals = {
        "WATCH": CatalystSignal(
            symbol="WATCH",
            score_delta=8,
            provider="fmp",
            reasons=["Fresh partnership catalyst."],
            events=["News: partnership"],
        ),
        "SKIP": CatalystSignal(
            symbol="SKIP",
            score_delta=25,
            provider="fmp",
            reasons=["Fresh partnership catalyst."],
            events=["News: partnership"],
        ),
    }

    enriched = {
        score.symbol: score
        for score in apply_catalyst_signals(
            [watch, skip],
            signals=signals,
            alert_threshold=78,
            budget=250,
        )
    }

    assert enriched["WATCH"].action == "candidate"
    assert enriched["WATCH"].suggested_amount == 250
    assert enriched["SKIP"].action == "watch"
    assert enriched["SKIP"].suggested_amount == 0
