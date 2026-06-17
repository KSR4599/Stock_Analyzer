from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal


FMP_BASE_URL = "https://financialmodelingprep.com/stable"

POSITIVE_KEYWORDS = {
    "accelerator",
    "acquisition",
    "ai",
    "approval",
    "artificial intelligence",
    "beats",
    "collaboration",
    "contract",
    "data center",
    "deal",
    "earnings beat",
    "gpu",
    "guidance raised",
    "hbm",
    "launch",
    "memory",
    "partnership",
    "price target raised",
    "raises guidance",
    "semiconductor",
    "strategic investment",
    "upgrade",
}

MOONSHOT_THEME_KEYWORDS = {
    "ai",
    "artificial intelligence",
    "autonomous",
    "chip",
    "data center",
    "defense",
    "gpu",
    "hbm",
    "inference",
    "memory",
    "nuclear",
    "quantum",
    "robotics",
    "semiconductor",
    "space",
}

NEGATIVE_KEYWORDS = {
    "bankruptcy",
    "class action",
    "cuts guidance",
    "delisting",
    "dilution",
    "downgrade",
    "fraud",
    "investigation",
    "lawsuit",
    "misses",
    "offering",
    "price target cut",
    "recall",
    "sec probe",
    "share sale",
    "subpoena",
}


class FmpApiError(RuntimeError):
    """Raised for FMP API failures without exposing the API key."""


@dataclass(frozen=True)
class FmpEndpointCheck:
    name: str
    ok: bool
    item_count: int = 0
    message: str = ""


class FmpCatalystProvider(CatalystProvider):
    name = "fmp"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 20.0,
        lookback_hours: int = 72,
        max_news_articles: int = 6,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.lookback_hours = lookback_hours
        self.max_news_articles = max_news_articles

    def fetch_signals(self, symbols: list[str], run_at: datetime) -> dict[str, CatalystSignal]:
        signals: dict[str, CatalystSignal] = {}
        for symbol in symbols:
            signals[symbol] = self._fetch_symbol_signal(symbol, run_at)
        return signals

    def _fetch_symbol_signal(self, symbol: str, run_at: datetime) -> CatalystSignal:
        try:
            news = self._get("news/stock", {"symbols": symbol, "limit": self.max_news_articles})
            earnings = self._get("earnings", {"symbol": symbol, "limit": 6})
            grades = self._get("grades", {"symbol": symbol, "limit": 6})
            price_targets = self._get("price-target-summary", {"symbol": symbol})
        except FmpApiError as exc:
            return CatalystSignal(
                symbol=symbol,
                provider=self.name,
                risks=[f"FMP catalyst fetch failed: {exc}"],
            )

        return build_fmp_signal(
            symbol=symbol,
            run_at=run_at,
            news=_as_list(news),
            earnings=_as_list(earnings),
            grades=_as_list(grades),
            price_targets=_as_list(price_targets),
            lookback_hours=self.lookback_hours,
        )

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        try:
            response = requests.get(
                f"{FMP_BASE_URL}/{endpoint}",
                params={**params, "apikey": self.api_key},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FmpApiError(f"{endpoint} request failed: {type(exc).__name__}") from None

        if response.status_code in {401, 403}:
            raise FmpApiError(f"{endpoint} authorization failed")
        if response.status_code == 429:
            raise FmpApiError(f"{endpoint} rate limit reached")
        if not response.ok:
            raise FmpApiError(f"{endpoint} failed with HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError:
            raise FmpApiError(f"{endpoint} returned invalid JSON") from None


def run_fmp_smoke_test(
    api_key: str,
    symbol: str = "NVDA",
    timeout_seconds: float = 20.0,
) -> list[FmpEndpointCheck]:
    provider = FmpCatalystProvider(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_news_articles=2,
    )
    endpoints = [
        ("profile", "profile", {"symbol": symbol}),
        ("stock_news", "news/stock", {"symbols": symbol, "limit": 2}),
        ("earnings", "earnings", {"symbol": symbol, "limit": 2}),
        ("analyst_grades", "grades", {"symbol": symbol, "limit": 2}),
        ("price_target_summary", "price-target-summary", {"symbol": symbol}),
    ]

    checks: list[FmpEndpointCheck] = []
    for name, endpoint, params in endpoints:
        try:
            payload = provider._get(endpoint, params)
        except FmpApiError as exc:
            checks.append(FmpEndpointCheck(name=name, ok=False, message=str(exc)))
            continue
        checks.append(
            FmpEndpointCheck(
                name=name,
                ok=True,
                item_count=len(_as_list(payload)),
                message="ok",
            )
        )
    return checks


def build_fmp_signal(
    symbol: str,
    run_at: datetime,
    news: list[dict[str, Any]],
    earnings: list[dict[str, Any]],
    grades: list[dict[str, Any]],
    price_targets: list[dict[str, Any]],
    lookback_hours: int = 72,
) -> CatalystSignal:
    score = 0.0
    confidence = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []

    news_score, news_reasons, news_risks, news_events, news_confidence = _score_news(
        symbol=symbol,
        run_at=run_at,
        news=news,
        lookback_hours=lookback_hours,
    )
    score += news_score
    confidence += news_confidence
    reasons.extend(news_reasons)
    risks.extend(news_risks)
    events.extend(news_events)

    earnings_score, earnings_reasons, earnings_risks, earnings_events, earnings_confidence = _score_earnings(
        run_at=run_at,
        earnings=earnings,
    )
    score += earnings_score
    confidence += earnings_confidence
    reasons.extend(earnings_reasons)
    risks.extend(earnings_risks)
    events.extend(earnings_events)

    grades_score, grades_reasons, grades_risks, grades_events, grades_confidence = _score_grades(grades)
    score += grades_score
    confidence += grades_confidence
    reasons.extend(grades_reasons)
    risks.extend(grades_risks)
    events.extend(grades_events)

    target_score, target_reasons, target_risks, target_events, target_confidence = _score_price_targets(price_targets)
    score += target_score
    confidence += target_confidence
    reasons.extend(target_reasons)
    risks.extend(target_risks)
    events.extend(target_events)

    if not reasons and not risks:
        reasons.append("No fresh FMP catalyst found in the enrichment window.")

    return CatalystSignal(
        symbol=symbol,
        score_delta=round(max(min(score, 25), -25), 1),
        confidence=round(max(min(confidence, 1.0), 0.0), 2),
        provider="fmp",
        reasons=_dedupe(reasons)[:5],
        risks=_dedupe(risks)[:5],
        events=_dedupe(events)[:6],
    )


def _score_news(
    symbol: str,
    run_at: datetime,
    news: list[dict[str, Any]],
    lookback_hours: int,
) -> tuple[float, list[str], list[str], list[str], float]:
    cutoff = _to_naive_utc(run_at) - timedelta(hours=lookback_hours)
    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    fresh_count = 0
    positive_hits = 0
    negative_hits = 0
    theme_hits: set[str] = set()

    for article in news:
        published_at = _parse_date(
            _first(article, "publishedDate", "date", "published_at", "publishedAt")
        )
        if published_at is not None and published_at < cutoff:
            continue

        title = str(_first(article, "title", "headline") or "").strip()
        text = " ".join(
            str(_first(article, key) or "")
            for key in ["title", "headline", "text", "summary", "content"]
        ).lower()
        if not title and not text.strip():
            continue

        fresh_count += 1
        positive = _keyword_hits(text, POSITIVE_KEYWORDS)
        negative = _keyword_hits(text, NEGATIVE_KEYWORDS)
        themes = _keyword_hits(text, MOONSHOT_THEME_KEYWORDS)
        positive_hits += len(positive)
        negative_hits += len(negative)
        theme_hits.update(themes)
        events.append(f"News: {title[:140]}")

    if fresh_count:
        score += min(fresh_count * 1.0, 4)
        if positive_hits:
            score += min(positive_hits * 1.5, 8)
            reasons.append(f"{symbol} has {positive_hits} positive catalyst keyword hit(s) in recent news.")
        if theme_hits:
            score += min(len(theme_hits) * 1.5, 6)
            reasons.append(f"Theme heat detected: {', '.join(sorted(theme_hits)[:4])}.")
        if negative_hits:
            score -= min(negative_hits * 2.5, 12)
            risks.append(f"{symbol} has {negative_hits} negative catalyst keyword hit(s) in recent news.")
        confidence = min(0.1 + fresh_count * 0.08, 0.35)
    else:
        confidence = 0.0

    return score, reasons, risks, events, confidence


def _score_earnings(
    run_at: datetime,
    earnings: list[dict[str, Any]],
) -> tuple[float, list[str], list[str], list[str], float]:
    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    confidence = 0.0
    run_day = _to_naive_utc(run_at).date()

    for item in earnings[:6]:
        date_value = _parse_date(_first(item, "date", "earningDate", "pricedate"))
        if date_value is None:
            continue
        days_away = (date_value.date() - run_day).days
        eps_actual = _to_float(_first(item, "epsActual", "actualEps", "eps"))
        eps_estimated = _to_float(_first(item, "epsEstimated", "estimatedEps", "epsEstimated"))
        revenue_actual = _to_float(_first(item, "revenueActual", "actualRevenue"))
        revenue_estimated = _to_float(_first(item, "revenueEstimated", "estimatedRevenue"))

        if -3 <= days_away <= 2:
            confidence += 0.18
            events.append(f"Earnings near now: {date_value.date().isoformat()}")
            if eps_actual is not None and eps_estimated not in (None, 0):
                eps_surprise = (eps_actual / eps_estimated - 1) * 100
                if eps_surprise >= 10:
                    score += 6
                    reasons.append(f"Recent EPS surprise was {eps_surprise:+.1f}%.")
                elif eps_surprise <= -10:
                    score -= 7
                    risks.append(f"Recent EPS miss was {eps_surprise:+.1f}%.")
            if revenue_actual is not None and revenue_estimated not in (None, 0):
                revenue_surprise = (revenue_actual / revenue_estimated - 1) * 100
                if revenue_surprise >= 5:
                    score += 4
                    reasons.append(f"Recent revenue surprise was {revenue_surprise:+.1f}%.")
                elif revenue_surprise <= -5:
                    score -= 5
                    risks.append(f"Recent revenue miss was {revenue_surprise:+.1f}%.")
        elif 0 < days_away <= 10:
            score += 1.5
            confidence += 0.12
            events.append(f"Upcoming earnings: {date_value.date().isoformat()}")
            risks.append("Upcoming earnings can create gap risk.")

    return score, reasons, risks, events, min(confidence, 0.25)


def _score_grades(grades: list[dict[str, Any]]) -> tuple[float, list[str], list[str], list[str], float]:
    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    confidence = 0.0

    for grade in grades[:4]:
        text = " ".join(str(value) for value in grade.values()).lower()
        firm = str(_first(grade, "gradingCompany", "firm", "analystRatingsCompany") or "Analyst")
        new_grade = str(_first(grade, "newGrade", "grade", "newRating", "rating") or "").strip()
        if any(word in text for word in ["upgrade", "overweight", "outperform", "buy", "strong buy"]):
            score += 3.5
            confidence += 0.08
            reasons.append(f"{firm} analyst signal is positive ({new_grade or 'upgrade/buy'}).")
            events.append(f"Analyst positive: {firm} {new_grade}".strip())
        if any(word in text for word in ["downgrade", "underweight", "underperform", "sell", "reduce"]):
            score -= 4.0
            confidence += 0.08
            risks.append(f"{firm} analyst signal is negative ({new_grade or 'downgrade/sell'}).")
            events.append(f"Analyst negative: {firm} {new_grade}".strip())

    return score, reasons, risks, events, min(confidence, 0.2)


def _score_price_targets(
    price_targets: list[dict[str, Any]],
) -> tuple[float, list[str], list[str], list[str], float]:
    if not price_targets:
        return 0.0, [], [], [], 0.0

    item = price_targets[0]
    last_price = _to_float(_first(item, "lastPrice", "price", "currentPrice"))
    target = _to_float(
        _first(
            item,
            "priceTargetAverage",
            "targetConsensus",
            "priceTarget",
            "targetHigh",
            "priceTargetHigh",
        )
    )
    if last_price in (None, 0) or target is None:
        return 0.0, [], [], [], 0.0

    upside = (target / last_price - 1) * 100
    events = [f"Analyst target upside: {upside:+.1f}%"]
    if upside >= 60:
        return 7.0, [f"Analyst target upside is {upside:+.1f}%."], [], events, 0.12
    if upside >= 30:
        return 4.5, [f"Analyst target upside is {upside:+.1f}%."], [], events, 0.10
    if upside <= -20:
        return -5.0, [], [f"Analyst target downside is {upside:+.1f}%."], events, 0.10
    return 0.0, [], [], events, 0.04


def _keyword_hits(text: str, keywords: set[str]) -> set[str]:
    return {keyword for keyword in keywords if keyword in text}


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_naive_utc(parsed)


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(tz=None).replace(tzinfo=None)
    return value


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ["data", "results", "items"]:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped
