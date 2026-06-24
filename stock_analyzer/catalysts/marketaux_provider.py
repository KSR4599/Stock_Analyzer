from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import NewsItem
from stock_analyzer.catalysts.news import prepare_news_items, score_news_items


MARKETAUX_NEWS_URL = "https://api.marketaux.com/v1/news/all"


class MarketauxApiError(RuntimeError):
    """Raised for Marketaux failures without exposing the API token."""


@dataclass(frozen=True)
class MarketauxEndpointCheck:
    name: str
    ok: bool
    item_count: int = 0
    message: str = ""


class MarketauxCatalystProvider(CatalystProvider):
    name = "marketaux"

    def __init__(
        self,
        api_token: str,
        timeout_seconds: float = 20.0,
        lookback_hours: int = 72,
        min_match_score: float = 10.0,
        state_store: Any | None = None,
    ) -> None:
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.lookback_hours = lookback_hours
        self.min_match_score = min_match_score
        self.state_store = state_store
        self.request_count = 0

    def fetch_signals(
        self,
        symbols: list[str],
        run_at: datetime,
    ) -> dict[str, CatalystSignal]:
        self.request_count = 0
        return {
            symbol: self._fetch_symbol_signal(symbol, run_at)
            for symbol in symbols
        }

    def _fetch_symbol_signal(
        self,
        symbol: str,
        run_at: datetime,
    ) -> CatalystSignal:
        try:
            payload = self._get_news(symbol, run_at)
        except MarketauxApiError as exc:
            return CatalystSignal(
                symbol=symbol,
                provider=self.name,
                risks=[f"Marketaux {exc}"],
            )
        news_items = normalize_marketaux_news(
            symbol=symbol,
            payload=payload,
            min_match_score=self.min_match_score,
        )
        prepared = prepare_news_items(news_items, max_clusters=3)
        contributions, reasons, risks, events = score_news_items(
            prepared,
            run_at=run_at,
            provider=self.name,
        )
        if not prepared:
            reasons.append("No sufficiently relevant Marketaux news found.")
        return aggregate_signal(
            symbol=symbol,
            provider=self.name,
            contributions=contributions,
            reasons=reasons,
            risks=risks,
            events=events,
            news_items=prepared,
        )

    def _get_news(self, symbol: str, run_at: datetime) -> dict[str, Any]:
        cache_key = f"news:{symbol}"
        if self.state_store is not None:
            cached = self.state_store.get_provider_cache(
                self.name,
                cache_key,
                max_age_hours=2.5,
            )
            if cached is not None and isinstance(cached[0], dict):
                self.state_store.record_provider_call(
                    self.name,
                    "news",
                    symbol,
                    True,
                    "cache",
                    item_count=len(cached[0].get("data", [])),
                    cache_hit=True,
                    message="cache hit",
                )
                return cached[0]
        payload = self._get(
            {
                "symbols": symbol,
                "filter_entities": "true",
                "must_have_entities": "true",
                "group_similar": "true",
                "language": "en",
                "limit": 3,
                "published_after": (
                    (run_at.astimezone(timezone.utc) - timedelta(hours=self.lookback_hours))
                    .replace(tzinfo=None)
                    .isoformat(timespec="seconds")
                ),
            },
            symbol=symbol,
        )
        if self.state_store is not None:
            self.state_store.set_provider_cache(self.name, cache_key, payload)
        return payload

    def _get(self, params: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
        self.request_count += 1
        safe_params = {**params, "api_token": self.api_token}
        try:
            response = requests.get(
                MARKETAUX_NEWS_URL,
                params=safe_params,
                headers={"Accept": "application/json", "User-Agent": "stock-analyzer/0.1"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            self._audit(symbol, False, type(exc).__name__, 0, "request failed")
            raise MarketauxApiError(f"news request failed: {type(exc).__name__}") from None
        if response.status_code in {401, 403}:
            self._audit(symbol, False, "authorization", 0, "authorization failed")
            raise MarketauxApiError("news authorization failed")
        if response.status_code == 429:
            self._audit(symbol, False, "rate_limit", 0, "rate limit reached")
            raise MarketauxApiError("news rate limit reached")
        if not response.ok:
            self._audit(symbol, False, f"http_{response.status_code}", 0, "request failed")
            raise MarketauxApiError(f"news failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            self._audit(symbol, False, "invalid_json", 0, "invalid JSON")
            raise MarketauxApiError("news returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise MarketauxApiError("news returned an unexpected payload")
        count = len(payload.get("data", [])) if isinstance(payload.get("data"), list) else 0
        self._audit(symbol, True, "ok", count, "ok")
        return payload

    def _audit(
        self,
        symbol: str | None,
        ok: bool,
        status: str,
        item_count: int,
        message: str,
    ) -> None:
        if self.state_store is not None:
            self.state_store.record_provider_call(
                self.name,
                "news",
                symbol,
                ok,
                status,
                item_count=item_count,
                message=message,
            )


def normalize_marketaux_news(
    symbol: str,
    payload: dict[str, Any],
    min_match_score: float = 10.0,
) -> list[NewsItem]:
    result: list[NewsItem] = []
    for article in payload.get("data", []):
        if not isinstance(article, dict):
            continue
        matching_entities = [
            entity
            for entity in article.get("entities", [])
            if isinstance(entity, dict)
            and str(entity.get("symbol") or "").upper() == symbol.upper()
            and _to_float(entity.get("match_score")) is not None
            and float(entity["match_score"]) >= min_match_score
        ]
        if not matching_entities:
            continue
        strongest = max(
            matching_entities,
            key=lambda entity: float(entity.get("match_score") or 0),
        )
        match_score = float(strongest.get("match_score") or 0)
        sentiment = _to_float(strongest.get("sentiment_score"))
        result.append(
            NewsItem(
                symbol=symbol,
                headline=str(article.get("title") or "").strip(),
                published_at=_parse_datetime(article.get("published_at")),
                source=str(article.get("source") or "Marketaux"),
                url=str(article.get("url") or ""),
                related_symbols=[symbol],
                sentiment=sentiment,
                relevance=min(match_score / 30.0, 1.0),
            )
        )
    return result


def run_marketaux_smoke_test(
    api_token: str,
    symbol: str = "NVDA",
    timeout_seconds: float = 20.0,
) -> list[MarketauxEndpointCheck]:
    provider = MarketauxCatalystProvider(
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )
    try:
        payload = provider._get(
            {
                "symbols": symbol,
                "filter_entities": "true",
                "must_have_entities": "true",
                "group_similar": "true",
                "language": "en",
                "limit": 3,
            },
            symbol=symbol,
        )
    except MarketauxApiError as exc:
        return [MarketauxEndpointCheck(name="company_news", ok=False, message=str(exc))]
    items = normalize_marketaux_news(symbol, payload)
    newest = max(
        (item.published_at for item in items if item.published_at is not None),
        default=None,
    )
    message = "ok"
    if newest is not None:
        message += f"; newest={newest.isoformat()}"
    return [
        MarketauxEndpointCheck(
            name="company_news",
            ok=True,
            item_count=len(items),
            message=message,
        )
    ]


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
