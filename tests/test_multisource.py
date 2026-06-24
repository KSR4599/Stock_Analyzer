from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.alpha_vantage_provider import (
    AlphaVantageApiError,
    AlphaVantageCatalystProvider,
    build_alpha_vantage_signal,
)
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.composite_provider import CompositeCatalystProvider
from stock_analyzer.catalysts.finnhub_provider import (
    _normalize_finnhub_news,
    build_finnhub_signal,
)
from stock_analyzer.catalysts.fred_provider import (
    FredApiError,
    FredMarketContextProvider,
    build_market_context,
)
from stock_analyzer.catalysts.marketaux_provider import (
    MarketauxApiError,
    MarketauxCatalystProvider,
    normalize_marketaux_news,
)
from stock_analyzer.catalysts.models import NewsItem, SignalContribution
from stock_analyzer.catalysts.news import prepare_news_items, score_news_items
from stock_analyzer.catalysts.sec_provider import (
    build_sec_fundamental_snapshot,
    parse_form4_transactions,
)


RUN_AT = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)


def test_ambiguous_arm_news_requires_company_context() -> None:
    items = _normalize_finnhub_news(
        "ARM",
        [
            {
                "datetime": 1781780000,
                "headline": "Robotic arm market expands rapidly",
                "summary": "Industrial automation demand is growing.",
                "related": "ARM",
            },
            {
                "datetime": 1781780100,
                "headline": "Arm Holdings launches new AI chip",
                "summary": "ARM announced a semiconductor design.",
                "related": "ARM,NVDA",
            },
        ],
        max_articles=6,
    )

    assert len(items) == 1
    assert items[0].headline.startswith("Arm Holdings")


def test_similar_headlines_are_scored_once() -> None:
    items = prepare_news_items(
        [
            NewsItem(
                symbol="NVDA",
                headline="Nvidia announces major AI data center partnership",
                published_at=RUN_AT,
                source="one",
                relevance=1.0,
            ),
            NewsItem(
                symbol="NVDA",
                headline="Nvidia announces a major data center AI partnership",
                published_at=RUN_AT,
                source="two",
                relevance=1.0,
            ),
        ],
        max_clusters=3,
        similarity_threshold=0.7,
    )

    assert len(items) == 1


def test_stale_news_has_no_score() -> None:
    stale = NewsItem(
        symbol="NVDA",
        headline="Nvidia announces AI partnership",
        published_at=RUN_AT - timedelta(days=5),
        source="example",
        relevance=1.0,
        cluster_id="stale",
    )

    contributions, _, _, _ = score_news_items([stale], RUN_AT, "test")

    assert contributions == []


def test_static_consensus_is_context_only_but_change_scores() -> None:
    static = build_finnhub_signal(
        symbol="NVDA",
        run_at=RUN_AT,
        news=[],
        earnings=[],
        recommendations=[
            {
                "period": "2026-06-01",
                "strongBuy": 8,
                "buy": 2,
                "hold": 2,
                "sell": 0,
                "strongSell": 0,
            }
        ],
    )
    improving = build_finnhub_signal(
        symbol="NVDA",
        run_at=RUN_AT,
        news=[],
        earnings=[],
        recommendations=[
            {
                "period": "2026-06-01",
                "strongBuy": 8,
                "buy": 2,
                "hold": 2,
                "sell": 0,
                "strongSell": 0,
            },
            {
                "period": "2026-05-01",
                "strongBuy": 3,
                "buy": 2,
                "hold": 5,
                "sell": 2,
                "strongSell": 0,
            },
        ],
    )

    assert static.score_delta == 0
    assert improving.score_delta > 0


def test_category_and_total_caps_are_enforced() -> None:
    signal = aggregate_signal(
        symbol="CAP",
        provider="multi",
        contributions=[
            SignalContribution("news", 5, 0.1, "a", "one", "1"),
            SignalContribution("news", 5, 0.1, "b", "two", "2"),
            SignalContribution("earnings", 4, 0.1, "c", "three", "3"),
            SignalContribution("fundamentals_analyst", 4, 0.1, "d", "four", "4"),
        ],
    )

    assert signal.score_delta == 10
    assert sum(
        item.score_delta for item in signal.contributions if item.category == "news"
    ) <= 6


def test_cross_provider_same_event_adds_confidence_not_score() -> None:
    signal = aggregate_signal(
        symbol="NVDA",
        provider="multi",
        contributions=[
            SignalContribution("news", 2, 0.1, "finnhub", "story", "same"),
            SignalContribution("news", 1.5, 0.1, "marketaux", "story", "same"),
        ],
    )

    assert signal.score_delta == 2
    assert signal.contributions[0].confidence == 0.15


def test_marketaux_requires_match_score() -> None:
    payload = {
        "data": [
            {
                "title": "Relevant story",
                "published_at": "2026-06-18T10:00:00Z",
                "source": "Example",
                "url": "https://example.com/relevant",
                "entities": [
                    {
                        "symbol": "NVDA",
                        "match_score": 18,
                        "sentiment_score": 0.5,
                    }
                ],
            },
            {
                "title": "Weak story",
                "published_at": "2026-06-18T10:00:00Z",
                "source": "Example",
                "url": "https://example.com/weak",
                "entities": [{"symbol": "NVDA", "match_score": 2}],
            },
        ]
    }

    items = normalize_marketaux_news("NVDA", payload, min_match_score=10)

    assert len(items) == 1
    assert items[0].sentiment == 0.5


def test_form4_parser_keeps_transaction_codes() -> None:
    xml = """
    <ownershipDocument>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>1000</value></transactionShares>
            <transactionPricePerShare><value>75</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>5000</value></transactionShares>
            <transactionPricePerShare><value>0</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>
    """

    transactions = parse_form4_transactions(xml, "accession")

    assert transactions[0]["code"] == "P"
    assert transactions[0]["value"] == 75_000
    assert transactions[1]["code"] == "A"


def test_sec_company_facts_detects_growth_and_dilution() -> None:
    company_facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _fact("2025-03-31", 100),
                            _fact("2025-06-30", 110),
                            _fact("2025-09-30", 120),
                            _fact("2025-12-31", 130),
                            _fact("2026-03-31", 150),
                        ]
                    }
                },
                "NetIncomeLoss": {"units": {"USD": [_fact("2026-03-31", 15)]}},
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            _fact("2025-03-31", 100),
                            _fact("2025-06-30", 102),
                            _fact("2025-09-30", 104),
                            _fact("2025-12-31", 106),
                            _fact("2026-03-31", 112),
                        ]
                    }
                }
            },
        }
    }

    snapshot, contributions, reasons, risks = build_sec_fundamental_snapshot(
        "TEST",
        RUN_AT,
        company_facts,
    )

    assert snapshot is not None
    assert snapshot.metrics["revenue_growth_yoy_pct"] == 50
    assert any(item.score_delta > 0 for item in contributions)
    assert any("Shares outstanding" in risk for risk in risks)


def test_alpha_vantage_scores_estimate_revisions() -> None:
    signal = build_alpha_vantage_signal(
        "TEST",
        RUN_AT,
        overview={
            "QuarterlyRevenueGrowthYOY": "0.25",
            "QuarterlyEarningsGrowthYOY": "0.30",
            "ProfitMargin": "0.12",
            "AnalystTargetPrice": "150",
        },
        estimates={
            "estimates": [
                {
                    "date": "2026-06-30",
                    "horizon": "fiscal quarter",
                    "eps_estimate_average": "1.10",
                    "eps_estimate_average_30_days_ago": "1.00",
                    "eps_estimate_revision_up_trailing_30_days": "8",
                    "eps_estimate_revision_down_trailing_30_days": "2",
                }
            ]
        },
    )

    assert 0 < signal.score_delta <= 4
    assert signal.fundamental_snapshot is not None


def test_alpha_vantage_uses_stale_cache_when_budget_exhausted() -> None:
    class Store:
        def get_provider_cache(self, provider, cache_key, max_age_hours=None):
            if max_age_hours is None:
                return ({"Symbol": "TEST"}, RUN_AT - timedelta(days=2))
            return None

        def count_provider_calls_since(self, provider, since):
            return 20

        def record_provider_call(self, *args, **kwargs):
            return None

    provider = AlphaVantageCatalystProvider(
        api_key="secret",
        state_store=Store(),
        daily_call_budget=20,
    )

    payload = provider._get_cached("OVERVIEW", "TEST")

    assert payload["Symbol"] == "TEST"
    assert provider.request_count == 0


def test_alpha_vantage_paces_remote_requests(monkeypatch) -> None:
    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"Symbol": "TEST"}

    monotonic_values = iter([100.0, 102.0, 112.5])
    sleeps: list[float] = []
    monkeypatch.setattr(
        "stock_analyzer.catalysts.alpha_vantage_provider.time_module.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "stock_analyzer.catalysts.alpha_vantage_provider.time_module.sleep",
        sleeps.append,
    )
    monkeypatch.setattr(
        "stock_analyzer.catalysts.alpha_vantage_provider.requests.get",
        lambda *args, **kwargs: Response(),
    )
    provider = AlphaVantageCatalystProvider(
        api_key="secret",
        min_request_interval_seconds=12.5,
    )

    provider._get("OVERVIEW", "TEST")
    provider._get("EARNINGS_ESTIMATES", "TEST")

    assert sleeps == [10.5]


def test_macro_context_never_adds_positive_score() -> None:
    context, contributions, risks = build_market_context(
        RUN_AT,
        {
            "vix": 35,
            "high_yield_spread": 5.5,
            "treasury_2y": 4.5,
            "treasury_10y": 4.0,
            "benchmarks_below_50d": 3,
        },
    )

    assert context.regime == "risk_off"
    assert sum(item.score_delta for item in contributions) == -5
    assert risks


def test_new_provider_errors_do_not_leak_keys(monkeypatch) -> None:
    class Response:
        status_code = 401
        ok = False

    monkeypatch.setattr(
        "stock_analyzer.catalysts.marketaux_provider.requests.get",
        lambda *args, **kwargs: Response(),
    )
    monkeypatch.setattr(
        "stock_analyzer.catalysts.alpha_vantage_provider.requests.get",
        lambda *args, **kwargs: Response(),
    )
    monkeypatch.setattr(
        "stock_analyzer.catalysts.fred_provider.requests.get",
        lambda *args, **kwargs: Response(),
    )

    checks = [
        (
            MarketauxCatalystProvider("marketaux-secret"),
            lambda provider: provider._get({"symbols": "NVDA"}),
            MarketauxApiError,
            "marketaux-secret",
        ),
        (
            AlphaVantageCatalystProvider("alpha-secret"),
            lambda provider: provider._get("OVERVIEW", "NVDA"),
            AlphaVantageApiError,
            "alpha-secret",
        ),
        (
            FredMarketContextProvider("fred-secret"),
            lambda provider: provider._request_latest("VIXCLS"),
            FredApiError,
            "fred-secret",
        ),
    ]
    for provider, call, error_type, secret in checks:
        try:
            call(provider)
        except error_type as exc:
            assert secret not in str(exc)
        else:
            raise AssertionError(f"expected {error_type.__name__}")


def test_composite_keeps_other_provider_when_one_fails() -> None:
    class FailingProvider(CatalystProvider):
        name = "failing"

        def fetch_signals(self, symbols, run_at):
            raise RuntimeError("boom")

    class WorkingProvider(CatalystProvider):
        name = "working"

        def fetch_signals(self, symbols, run_at):
            return {
                symbol: aggregate_signal(
                    symbol,
                    self.name,
                    [
                        SignalContribution(
                            "news",
                            1,
                            0.1,
                            self.name,
                            "working contribution",
                            "working",
                        )
                    ],
                )
                for symbol in symbols
            }

    provider = CompositeCatalystProvider(
        [(FailingProvider(), None), (WorkingProvider(), None)]
    )

    signal = provider.fetch_signals(["TEST"], RUN_AT)["TEST"]

    assert signal.score_delta == 1
    assert any("failing provider failed" in risk for risk in signal.risks)


def _fact(end: str, value: float) -> dict[str, object]:
    return {
        "end": end,
        "val": value,
        "form": "10-Q",
        "fp": "Q1",
        "filed": end,
    }
