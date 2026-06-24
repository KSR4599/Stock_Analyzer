from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import MarketContext, SignalContribution


FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {
    "vix": "VIXCLS",
    "treasury_2y": "DGS2",
    "treasury_10y": "DGS10",
    "high_yield_spread": "BAMLH0A0HYM2",
    "fed_funds": "DFF",
}


class FredApiError(RuntimeError):
    """Raised for FRED failures without exposing the API key."""


@dataclass(frozen=True)
class FredEndpointCheck:
    name: str
    ok: bool
    item_count: int = 0
    message: str = ""


class FredMarketContextProvider(CatalystProvider):
    name = "fred"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 20.0,
        state_store: Any | None = None,
        cache_hours: float = 12.0,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.state_store = state_store
        self.cache_hours = cache_hours
        self.request_count = 0
        self.market_histories: dict[str, Any] = {}

    def set_market_histories(self, histories: dict[str, Any]) -> None:
        self.market_histories = histories

    def fetch_signals(
        self,
        symbols: list[str],
        run_at: datetime,
    ) -> dict[str, CatalystSignal]:
        values: dict[str, float] = {}
        errors: list[str] = []
        for name, series_id in FRED_SERIES.items():
            try:
                values[name] = self._get_latest(series_id)
            except FredApiError as exc:
                errors.append(f"FRED {exc}")
        market_metrics = _market_trend_metrics(self.market_histories)
        context, contributions, risks = build_market_context(
            run_at,
            {**values, **market_metrics},
        )
        signals: dict[str, CatalystSignal] = {}
        for symbol in symbols:
            signals[symbol] = aggregate_signal(
                symbol=symbol,
                provider=self.name,
                contributions=contributions,
                risks=[*risks, *errors],
                events=[f"Market regime: {context.regime}"],
                market_context=context,
            )
        return signals

    def _get_latest(self, series_id: str) -> float:
        cache_key = f"series:{series_id}"
        if self.state_store is not None:
            cached = self.state_store.get_provider_cache(
                self.name,
                cache_key,
                max_age_hours=self.cache_hours,
            )
            if cached is not None:
                self.state_store.record_provider_call(
                    self.name,
                    "series_observations",
                    series_id,
                    True,
                    "cache",
                    item_count=1,
                    cache_hit=True,
                    message="cache hit",
                )
                return float(cached[0])
        value = self._request_latest(series_id)
        if self.state_store is not None:
            self.state_store.set_provider_cache(self.name, cache_key, value)
        return value

    def _request_latest(self, series_id: str) -> float:
        self.request_count += 1
        try:
            response = requests.get(
                FRED_OBSERVATIONS_URL,
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 10,
                },
                headers={"Accept": "application/json", "User-Agent": "stock-analyzer/0.1"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            self._audit(series_id, False, type(exc).__name__, "request failed")
            raise FredApiError(
                f"{series_id} request failed: {type(exc).__name__}"
            ) from None
        if response.status_code in {400, 401, 403}:
            self._audit(series_id, False, "authorization", "authorization failed")
            raise FredApiError(f"{series_id} authorization failed")
        if response.status_code == 429:
            self._audit(series_id, False, "rate_limit", "rate limit reached")
            raise FredApiError(f"{series_id} rate limit reached")
        if not response.ok:
            self._audit(series_id, False, f"http_{response.status_code}", "request failed")
            raise FredApiError(f"{series_id} failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            self._audit(series_id, False, "invalid_json", "invalid JSON")
            raise FredApiError(f"{series_id} returned invalid JSON") from None
        observations = payload.get("observations", []) if isinstance(payload, dict) else []
        for item in observations:
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            self._audit(series_id, True, "ok", "ok")
            return value
        self._audit(series_id, False, "no_data", "no numeric observation")
        raise FredApiError(f"{series_id} returned no numeric observation")

    def _audit(self, series_id: str, ok: bool, status: str, message: str) -> None:
        if self.state_store is not None:
            self.state_store.record_provider_call(
                self.name,
                "series_observations",
                series_id,
                ok,
                status,
                item_count=1 if ok else 0,
                message=message,
            )


def build_market_context(
    run_at: datetime,
    values: dict[str, float],
) -> tuple[MarketContext, list[SignalContribution], list[str]]:
    score = 0.0
    risks: list[str] = []
    vix = values.get("vix")
    spread = values.get("high_yield_spread")
    two_year = values.get("treasury_2y")
    ten_year = values.get("treasury_10y")
    below_50d = values.get("benchmarks_below_50d")
    if vix is not None:
        if vix >= 30:
            score -= 3.0
            risks.append(f"VIX is elevated at {vix:.1f}.")
        elif vix >= 22:
            score -= 1.5
            risks.append(f"VIX is above normal at {vix:.1f}.")
    if spread is not None:
        if spread >= 5:
            score -= 2.0
            risks.append(f"High-yield spread is stressed at {spread:.2f}.")
        elif spread >= 4:
            score -= 1.0
            risks.append(f"High-yield spread is elevated at {spread:.2f}.")
    if two_year is not None and ten_year is not None and ten_year - two_year < 0:
        score -= 1.0
        risks.append("The 10-year/2-year Treasury curve is inverted.")
    if below_50d is not None:
        if below_50d >= 3:
            score -= 2.0
            risks.append("At least three major equity benchmarks are below their 50-day averages.")
        elif below_50d == 2:
            score -= 1.0
            risks.append("Two major equity benchmarks are below their 50-day averages.")
    score = max(score, -5.0)
    regime = "risk_off" if score <= -3 else "cautious" if score < 0 else "neutral"
    context = MarketContext(
        as_of=run_at,
        provider="fred",
        regime=regime,
        metrics=values,
        reasons=risks,
    )
    contributions = []
    if score:
        contributions.append(
            SignalContribution(
                category="macro",
                score_delta=score,
                confidence=0.2,
                source="fred",
                summary=f"Macro regime is {regime}.",
                event_id=f"fred-regime-{run_at.date().isoformat()}",
                metadata=values,
            )
        )
    return context, contributions, risks


def run_fred_smoke_test(
    api_key: str,
    timeout_seconds: float = 20.0,
) -> list[FredEndpointCheck]:
    provider = FredMarketContextProvider(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    checks: list[FredEndpointCheck] = []
    for name, series_id in FRED_SERIES.items():
        try:
            value = provider._request_latest(series_id)
        except FredApiError as exc:
            checks.append(FredEndpointCheck(name=name, ok=False, message=str(exc)))
            continue
        checks.append(
            FredEndpointCheck(
                name=name,
                ok=True,
                item_count=1,
                message=f"ok; latest={value}",
            )
        )
    return checks


def _market_trend_metrics(histories: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    below_50d = 0
    observed = 0
    for symbol in ["SPY", "QQQ", "IWM", "SOXX"]:
        frame = histories.get(symbol)
        if frame is None or getattr(frame, "empty", True):
            continue
        columns = {str(column).lower(): column for column in frame.columns}
        close_column = columns.get("close")
        if close_column is None:
            continue
        close = frame[close_column].dropna()
        if len(close) < 50:
            continue
        observed += 1
        latest = float(close.iloc[-1])
        average = float(close.tail(50).mean())
        if latest < average:
            below_50d += 1
        if len(close) >= 22:
            metrics[f"{symbol.lower()}_return_21d_pct"] = (
                latest / float(close.iloc[-22]) - 1
            ) * 100
    if observed:
        metrics["benchmarks_below_50d"] = float(below_50d)
        metrics["benchmarks_observed"] = float(observed)
    return metrics
