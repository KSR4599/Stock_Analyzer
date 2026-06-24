from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import requests

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import NewsItem, SignalContribution
from stock_analyzer.catalysts.news import prepare_news_items, score_news_items


FINNHUB_BASE_URL = "https://api.finnhub.io/api/v1"


class FinnhubApiError(RuntimeError):
    """Raised for Finnhub API failures without exposing the API key."""


@dataclass(frozen=True)
class FinnhubEndpointCheck:
    name: str
    ok: bool
    item_count: int = 0
    message: str = ""


class FinnhubCatalystProvider(CatalystProvider):
    name = "finnhub"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 20.0,
        lookback_hours: int = 72,
        max_news_articles: int = 6,
        state_store: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.lookback_hours = lookback_hours
        self.max_news_articles = max_news_articles
        self.state_store = state_store
        self.request_count = 0

    def fetch_signals(self, symbols: list[str], run_at: datetime) -> dict[str, CatalystSignal]:
        self.request_count = 0
        return {
            symbol: self._fetch_symbol_signal(symbol, run_at)
            for symbol in symbols
        }

    def _fetch_symbol_signal(self, symbol: str, run_at: datetime) -> CatalystSignal:
        endpoint_errors: list[str] = []
        run_day = _to_naive_utc(run_at).date()
        news_from = run_day - timedelta(
            days=max(1, (self.lookback_hours + 23) // 24)
        )
        earnings_from = run_day - timedelta(days=3)
        earnings_to = run_day + timedelta(days=10)

        news = self._get_optional(
            "company-news",
            {
                "symbol": symbol,
                "from": news_from.isoformat(),
                "to": run_day.isoformat(),
            },
            endpoint_errors,
        )
        earnings_payload = self._get_optional(
            "calendar/earnings",
            {
                "symbol": symbol,
                "from": earnings_from.isoformat(),
                "to": earnings_to.isoformat(),
            },
            endpoint_errors,
        )
        recommendations = self._get_optional(
            "stock/recommendation",
            {"symbol": symbol},
            endpoint_errors,
        )
        news_items = _normalize_finnhub_news(
            symbol=symbol,
            payload=_as_list(news),
            max_articles=self.max_news_articles,
        )
        earnings_items = []
        if isinstance(earnings_payload, dict):
            earnings_items = _as_list(earnings_payload.get("earningsCalendar"))

        signal = build_finnhub_signal(
            symbol=symbol,
            run_at=run_at,
            news=news_items,
            earnings=earnings_items,
            recommendations=_as_list(recommendations),
            lookback_hours=self.lookback_hours,
        )
        if not endpoint_errors:
            return signal
        return CatalystSignal(
            symbol=signal.symbol,
            score_delta=signal.score_delta,
            confidence=signal.confidence,
            provider=signal.provider,
            reasons=signal.reasons,
            risks=_dedupe([*signal.risks, *endpoint_errors])[:5],
            events=signal.events,
            contributions=signal.contributions,
            news_items=signal.news_items,
            fundamental_snapshot=signal.fundamental_snapshot,
            market_context=signal.market_context,
        )

    def _get_optional(
        self,
        endpoint: str,
        params: dict[str, Any],
        errors: list[str],
    ) -> Any:
        symbol = str(params.get("symbol") or "")
        cache_key = f"{endpoint}:{symbol}"
        if self.state_store is not None:
            cached = self.state_store.get_provider_cache(
                self.name,
                cache_key,
                max_age_hours=2.5,
            )
            if cached is not None:
                self.state_store.record_provider_call(
                    self.name,
                    endpoint,
                    symbol,
                    True,
                    "cache",
                    item_count=_payload_item_count(cached[0]),
                    cache_hit=True,
                    message="cache hit",
                )
                return cached[0]
        try:
            payload = self._get(endpoint, params)
        except FinnhubApiError as exc:
            errors.append(f"Finnhub {exc}")
            return []
        if self.state_store is not None:
            self.state_store.set_provider_cache(self.name, cache_key, payload)
        return payload

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        self.request_count += 1
        try:
            response = requests.get(
                f"{FINNHUB_BASE_URL}/{endpoint}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "stock-analyzer/0.1",
                    "X-Finnhub-Token": self.api_key,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            self._audit(endpoint, params, False, type(exc).__name__, 0, "request failed")
            raise FinnhubApiError(f"{endpoint} request failed: {type(exc).__name__}") from None

        if response.status_code in {401, 403}:
            self._audit(endpoint, params, False, "authorization", 0, "authorization failed")
            raise FinnhubApiError(f"{endpoint} authorization or plan access failed")
        if response.status_code == 429:
            self._audit(endpoint, params, False, "rate_limit", 0, "rate limit reached")
            raise FinnhubApiError(f"{endpoint} rate limit reached")
        if not response.ok:
            self._audit(
                endpoint,
                params,
                False,
                f"http_{response.status_code}",
                0,
                "request failed",
            )
            raise FinnhubApiError(f"{endpoint} failed with HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            self._audit(endpoint, params, False, "invalid_json", 0, "invalid JSON")
            raise FinnhubApiError(f"{endpoint} returned invalid JSON") from None
        if isinstance(payload, dict) and payload.get("error"):
            self._audit(endpoint, params, False, "api_error", 0, "API error")
            raise FinnhubApiError(f"{endpoint} returned an API error")
        self._audit(
            endpoint,
            params,
            True,
            "ok",
            _payload_item_count(payload),
            "ok",
        )
        return payload

    def _audit(
        self,
        endpoint: str,
        params: dict[str, Any],
        ok: bool,
        status: str,
        item_count: int,
        message: str,
    ) -> None:
        if self.state_store is not None:
            self.state_store.record_provider_call(
                self.name,
                endpoint,
                str(params.get("symbol") or ""),
                ok,
                status,
                item_count=item_count,
                message=message,
            )


def run_finnhub_smoke_test(
    api_key: str,
    symbol: str = "NVDA",
    timeout_seconds: float = 20.0,
    run_at: datetime | None = None,
) -> list[FinnhubEndpointCheck]:
    provider = FinnhubCatalystProvider(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_news_articles=2,
    )
    run_day = _to_naive_utc(run_at or datetime.now(timezone.utc)).date()
    endpoints = [
        ("profile", "stock/profile2", {"symbol": symbol}),
        (
            "company_news",
            "company-news",
            {
                "symbol": symbol,
                "from": (run_day - timedelta(days=3)).isoformat(),
                "to": run_day.isoformat(),
            },
        ),
        (
            "earnings_calendar",
            "calendar/earnings",
            {
                "symbol": symbol,
                "from": (run_day - timedelta(days=3)).isoformat(),
                "to": (run_day + timedelta(days=10)).isoformat(),
            },
        ),
        ("recommendation_trends", "stock/recommendation", {"symbol": symbol}),
        ("price_target", "stock/price-target", {"symbol": symbol}),
    ]

    checks: list[FinnhubEndpointCheck] = []
    for name, endpoint, params in endpoints:
        try:
            payload = provider._get(endpoint, params)
        except FinnhubApiError as exc:
            checks.append(FinnhubEndpointCheck(name=name, ok=False, message=str(exc)))
            continue
        item_count, message = _summarize_endpoint(name, payload)
        checks.append(
            FinnhubEndpointCheck(
                name=name,
                ok=True,
                item_count=item_count,
                message=message,
            )
        )
    return checks


def build_finnhub_signal(
    symbol: str,
    run_at: datetime,
    news: list[dict[str, Any]] | list[NewsItem],
    earnings: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    price_target: dict[str, Any] | None = None,
    lookback_hours: int = 72,
) -> CatalystSignal:
    news_items = (
        list(news)
        if not news or isinstance(news[0], NewsItem)
        else _normalize_finnhub_news(symbol, _as_list(news), max_articles=6)
    )
    prepared_news = prepare_news_items(
        [item for item in news_items if isinstance(item, NewsItem)],
        max_clusters=3,
    )
    news_contributions, news_reasons, news_risks, news_events = score_news_items(
        prepared_news,
        run_at=run_at,
        provider="finnhub",
    )
    earnings_contributions, earnings_reasons, earnings_risks, earnings_events = (
        _score_earnings(run_at, earnings)
    )
    analyst_contributions, analyst_reasons, analyst_risks, analyst_events = (
        _score_recommendations(recommendations)
    )
    contributions = [
        *news_contributions,
        *earnings_contributions,
        *analyst_contributions,
    ]
    reasons = [*news_reasons, *earnings_reasons, *analyst_reasons]
    risks = [*news_risks, *earnings_risks, *analyst_risks]
    events = [*news_events, *earnings_events, *analyst_events]
    if not reasons and not risks and not events:
        reasons.append("No fresh Finnhub catalyst found in the enrichment window.")

    return aggregate_signal(
        symbol=symbol,
        provider="finnhub",
        contributions=contributions,
        reasons=reasons,
        risks=risks,
        events=events,
        news_items=prepared_news,
    )


def _score_recommendations(
    recommendations: list[dict[str, Any]],
) -> tuple[list[SignalContribution], list[str], list[str], list[str]]:
    if not recommendations:
        return [], [], [], []

    latest = recommendations[0]
    current = _recommendation_counts(latest)
    total = sum(current.values())
    if total == 0:
        return [], [], [], []
    bullish = current["strongBuy"] + current["buy"]
    bearish = current["sell"] + current["strongSell"]
    period = str(latest.get("period") or "latest period")
    events = [f"Analyst mix {period}: {bullish} buy, {current['hold']} hold, {bearish} sell"]
    if len(recommendations) < 2:
        return [], [], [], events

    previous = _recommendation_counts(recommendations[1])
    previous_total = sum(previous.values())
    if total < 5 or previous_total < 5:
        return [], [], [], events
    current_ratio = bullish / total
    previous_ratio = (previous["strongBuy"] + previous["buy"]) / previous_total
    change = current_ratio - previous_ratio
    if change >= 0.08:
        contribution = SignalContribution(
            category="fundamentals_analyst",
            score_delta=1.5,
            confidence=0.1,
            source="finnhub",
            summary=f"Buy-rating share improved by {change * 100:.1f} points.",
            event_id=f"finnhub-recommendation-{period}",
        )
        return [contribution], ["Analyst recommendation trend improved."], [], events
    if change <= -0.08:
        contribution = SignalContribution(
            category="fundamentals_analyst",
            score_delta=-2.0,
            confidence=0.1,
            source="finnhub",
            summary=f"Buy-rating share fell by {abs(change) * 100:.1f} points.",
            event_id=f"finnhub-recommendation-{period}",
        )
        return [contribution], [], ["Analyst recommendation trend weakened."], events
    return [], [], [], events


def _score_earnings(
    run_at: datetime,
    earnings: list[dict[str, Any]],
) -> tuple[list[SignalContribution], list[str], list[str], list[str]]:
    contributions: list[SignalContribution] = []
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    run_day = _to_naive_utc(run_at).date()
    for item in earnings[:6]:
        parsed = _parse_iso_date(item.get("date"))
        if parsed is None:
            continue
        days_away = (parsed.date() - run_day).days
        if 0 < days_away <= 10:
            risks.append("Upcoming earnings can create gap risk.")
            events.append(f"Upcoming earnings: {parsed.date().isoformat()}")
            continue
        if not -3 <= days_away <= 1:
            continue
        actual = _to_float(item.get("epsActual"))
        estimate = _to_float(item.get("epsEstimate"))
        if actual is None or estimate in (None, 0):
            continue
        surprise = (actual / estimate - 1) * 100
        if surprise >= 10:
            contributions.append(
                SignalContribution(
                    category="earnings",
                    score_delta=min(3.0, surprise / 10),
                    confidence=0.16,
                    source="finnhub",
                    summary=f"Recent EPS surprise was {surprise:+.1f}%.",
                    event_id=f"earnings-{parsed.date().isoformat()}",
                )
            )
            reasons.append(f"Recent EPS surprise was {surprise:+.1f}%.")
        elif surprise <= -10:
            contributions.append(
                SignalContribution(
                    category="earnings",
                    score_delta=max(-4.0, surprise / 8),
                    confidence=0.16,
                    source="finnhub",
                    summary=f"Recent EPS miss was {surprise:+.1f}%.",
                    event_id=f"earnings-{parsed.date().isoformat()}",
                )
            )
            risks.append(f"Recent EPS miss was {surprise:+.1f}%.")
        events.append(f"Recent earnings: {parsed.date().isoformat()}")
    return contributions, reasons, risks, events


def _normalize_finnhub_news(
    symbol: str,
    payload: list[dict[str, Any]],
    max_articles: int,
) -> list[NewsItem]:
    result: list[NewsItem] = []
    for article in sorted(
        payload,
        key=lambda item: _to_int(item.get("datetime")) or 0,
        reverse=True,
    ):
        headline = str(article.get("headline") or "").strip()
        summary = str(article.get("summary") or "").strip()
        related = [
            part.strip().upper()
            for part in str(article.get("related") or "").split(",")
            if part.strip()
        ]
        relevance = _finnhub_relevance(symbol, headline, summary, related)
        if relevance <= 0:
            continue
        timestamp = _to_int(article.get("datetime"))
        published_at = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if timestamp is not None
            else None
        )
        result.append(
            NewsItem(
                symbol=symbol,
                headline=headline,
                published_at=published_at,
                source=str(article.get("source") or "Finnhub"),
                url=str(article.get("url") or ""),
                related_symbols=related,
                relevance=relevance,
            )
        )
        if len(result) >= max_articles * 3:
            break
    return result


def _finnhub_relevance(
    symbol: str,
    headline: str,
    summary: str,
    related: list[str],
) -> float:
    text = f"{headline} {summary}"
    ticker_match = bool(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text)
    )
    related_match = symbol.upper() in related
    if symbol.upper() == "ARM":
        company_match = bool(
            re.search(
                r"\bArm(?: Holdings| Ltd| Limited|\s*\(ARM\)|'s)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        if not (ticker_match or company_match):
            return 0.0
        return 1.0 if ticker_match and related_match else 0.85
    if ticker_match and related_match:
        return 1.0
    if related_match:
        return 0.8
    if ticker_match:
        return 0.65
    return 0.0


def _recommendation_counts(item: dict[str, Any]) -> dict[str, int]:
    return {
        key: _to_int(item.get(key)) or 0
        for key in ["strongBuy", "buy", "hold", "sell", "strongSell"]
    }


def _parse_iso_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _summarize_endpoint(name: str, payload: Any) -> tuple[int, str]:
    item_count = _payload_item_count(payload)
    if name == "company_news":
        timestamps = [
            timestamp
            for item in _as_list(payload)
            if (timestamp := _to_int(item.get("datetime"))) is not None
        ]
        if timestamps:
            return item_count, f"ok; newest={_unix_to_iso(max(timestamps))}"
    if name == "earnings_calendar" and isinstance(payload, dict):
        dates = sorted(
            str(item.get("date"))
            for item in _as_list(payload.get("earningsCalendar"))
            if item.get("date")
        )
        if dates:
            return item_count, f"ok; dates={dates[0]}..{dates[-1]}"
    if name == "recommendation_trends":
        recommendations = _as_list(payload)
        if recommendations and recommendations[0].get("period"):
            return item_count, f"ok; latest_period={recommendations[0]['period']}"
    if name == "price_target" and isinstance(payload, dict):
        updated = str(payload.get("lastUpdated") or "").strip()
        if updated:
            return item_count, f"ok; last_updated={updated}"
    return item_count, "ok"


def _payload_item_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        if isinstance(payload.get("earningsCalendar"), list):
            return len(payload["earningsCalendar"])
        return 1 if payload else 0
    return 0


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _unix_to_iso(value: Any) -> str | None:
    timestamp = _to_int(value)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped
