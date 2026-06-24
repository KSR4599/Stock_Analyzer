from __future__ import annotations

from datetime import datetime

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.catalysts.finnhub_provider import (
    FinnhubApiError,
    FinnhubCatalystProvider,
    build_finnhub_signal,
    run_finnhub_smoke_test,
)
from stock_analyzer.catalysts.fmp_provider import FmpApiError, FmpCatalystProvider, build_fmp_signal, run_fmp_smoke_test
from stock_analyzer.catalysts.scoring import apply_catalyst_signals
from stock_analyzer.models import StockScore


RUN_AT = datetime(2026, 6, 15, 12, 0, 0)


def test_finnhub_signal_combines_news_earnings_and_recommendations() -> None:
    signal = build_finnhub_signal(
        symbol="MOON",
        run_at=RUN_AT,
        news=[
            {
                "datetime": 1781539200,
                "headline": "MOON announces AI data center chip partnership",
                "summary": "The company raised guidance after signing a major contract.",
            }
        ],
        earnings=[
            {
                "date": "2026-06-15",
                "epsActual": 1.2,
                "epsEstimate": 1.0,
                "revenueActual": 110,
                "revenueEstimate": 100,
            }
        ],
        recommendations=[
            {
                "period": "2026-06-01",
                "strongBuy": 6,
                "buy": 3,
                "hold": 2,
                "sell": 0,
                "strongSell": 0,
            },
            {
                "period": "2026-05-01",
                "strongBuy": 3,
                "buy": 2,
                "hold": 5,
                "sell": 1,
                "strongSell": 0,
            },
        ],
    )

    assert signal.provider == "finnhub"
    assert 0 < signal.score_delta <= 10
    assert any("recommendation trend improved" in reason.lower() for reason in signal.reasons)
    assert signal.contributions


def test_finnhub_http_error_does_not_leak_api_key(monkeypatch) -> None:
    class Response:
        status_code = 403
        ok = False

    request_headers = {}

    def fake_get(*args, **kwargs):
        request_headers.update(kwargs["headers"])
        return Response()

    monkeypatch.setattr("stock_analyzer.catalysts.finnhub_provider.requests.get", fake_get)
    provider = FinnhubCatalystProvider(api_key="secret-key")

    try:
        provider._get("stock/price-target", {"symbol": "ARM"})
    except FinnhubApiError as exc:
        assert "secret-key" not in str(exc)
        assert "plan access failed" in str(exc)
        assert provider.request_count == 1
        assert request_headers["X-Finnhub-Token"] == "secret-key"
    else:
        raise AssertionError("expected FinnhubApiError")


def test_finnhub_smoke_test_reports_endpoint_counts(monkeypatch) -> None:
    def fake_get(self, endpoint, params):
        if endpoint == "calendar/earnings":
            return {
                "earningsCalendar": [
                    {"symbol": params["symbol"], "date": "2026-06-18"}
                ]
            }
        if endpoint == "stock/price-target":
            return {
                "symbol": params["symbol"],
                "lastUpdated": "2026-06-14T00:00:00",
            }
        if endpoint == "stock/profile2":
            return {"symbol": params["symbol"]}
        if endpoint == "company-news":
            return [{"symbol": params["symbol"], "datetime": 1781539200}]
        if endpoint == "stock/recommendation":
            return [{"symbol": params["symbol"], "period": "2026-06-01"}]
        return [{"symbol": params["symbol"]}]

    monkeypatch.setattr(FinnhubCatalystProvider, "_get", fake_get)

    checks = run_finnhub_smoke_test(
        api_key="secret-key",
        symbol="NVDA",
        run_at=RUN_AT,
    )

    assert len(checks) == 5
    assert all(check.ok for check in checks)
    assert {check.name for check in checks} == {
        "profile",
        "company_news",
        "earnings_calendar",
        "recommendation_trends",
        "price_target",
    }
    messages = {check.name: check.message for check in checks}
    assert "newest=" in messages["company_news"]
    assert "dates=2026-06-18..2026-06-18" in messages["earnings_calendar"]
    assert "latest_period=2026-06-01" in messages["recommendation_trends"]
    assert "last_updated=2026-06-14" in messages["price_target"]


def test_finnhub_provider_keeps_partial_endpoint_results(monkeypatch) -> None:
    endpoints = []

    def fake_get(self, endpoint, params):
        endpoints.append(endpoint)
        if endpoint == "company-news":
            return [
                {
                    "datetime": 1781539200,
                    "headline": "ARM announces AI data center partnership",
                }
            ]
        if endpoint == "stock/recommendation":
            return [
                {
                    "period": "2026-06-01",
                    "strongBuy": 8,
                    "buy": 2,
                    "hold": 2,
                    "sell": 0,
                    "strongSell": 0,
                }
            ]
        return {"earningsCalendar": []}

    monkeypatch.setattr(FinnhubCatalystProvider, "_get", fake_get)
    provider = FinnhubCatalystProvider(api_key="secret-key")

    signal = provider.fetch_signals(["ARM"], RUN_AT)["ARM"]

    assert signal.score_delta > 0
    assert signal.events
    assert "stock/price-target" not in endpoints


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


def test_fmp_provider_keeps_partial_results_without_polluting_investment_risks(
    monkeypatch,
) -> None:
    def fake_get(self, endpoint, params):
        if endpoint == "grades":
            raise FmpApiError("grades authorization failed")
        if endpoint == "news/stock":
            return [
                {
                    "publishedDate": "2026-06-15T10:00:00",
                    "title": "NVDA announces AI data center partnership",
                }
            ]
        return []

    monkeypatch.setattr(FmpCatalystProvider, "_get", fake_get)
    provider = FmpCatalystProvider(api_key="secret-key")

    signal = provider.fetch_signals(["NVDA"], RUN_AT)["NVDA"]

    assert signal.score_delta > 0
    assert signal.events
    assert not any("authorization failed" in risk for risk in signal.risks)


def test_catalyst_endpoint_warning_is_preserved_without_scored_event() -> None:
    watch = StockScore(
        symbol="ARM",
        score=75,
        market_score=75,
        last_price=100,
        action="watch",
        suggested_amount=0,
        risk_level="medium",
        risks=["Market risk."],
    )
    signal = CatalystSignal(
        symbol="ARM",
        provider="fmp",
        risks=["FMP news/stock failed with HTTP 402"],
    )

    enriched = apply_catalyst_signals(
        [watch],
        signals={"ARM": signal},
        alert_threshold=78,
        budget=250,
    )[0]

    assert "FMP news/stock failed with HTTP 402" in enriched.risks
    assert "Market risk." in enriched.risks


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
    assert enriched["SKIP"].action == "skip"
    assert enriched["SKIP"].suggested_amount == 0
