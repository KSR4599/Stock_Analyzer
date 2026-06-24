from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import time as time_module
from typing import Any

import requests

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import FundamentalSnapshot, SignalContribution


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


class AlphaVantageApiError(RuntimeError):
    """Raised for Alpha Vantage failures without exposing the API key."""


@dataclass(frozen=True)
class AlphaVantageEndpointCheck:
    name: str
    ok: bool
    item_count: int = 0
    message: str = ""


class AlphaVantageCatalystProvider(CatalystProvider):
    name = "alpha_vantage"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 20.0,
        state_store: Any | None = None,
        daily_call_budget: int = 20,
        cache_hours: float = 24.0,
        min_request_interval_seconds: float = 12.5,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.state_store = state_store
        self.daily_call_budget = daily_call_budget
        self.cache_hours = cache_hours
        self.min_request_interval_seconds = min_request_interval_seconds
        self.request_count = 0
        self._last_request_at: float | None = None

    def fetch_signals(
        self,
        symbols: list[str],
        run_at: datetime,
    ) -> dict[str, CatalystSignal]:
        self.request_count = 0
        return {
            symbol: self._fetch_symbol_signal(symbol, run_at)
            for symbol in symbols[:10]
        }

    def _fetch_symbol_signal(
        self,
        symbol: str,
        run_at: datetime,
    ) -> CatalystSignal:
        errors: list[str] = []
        overview = self._get_optional("OVERVIEW", symbol, errors)
        estimates = self._get_optional("EARNINGS_ESTIMATES", symbol, errors)
        signal = build_alpha_vantage_signal(
            symbol=symbol,
            run_at=run_at,
            overview=overview if isinstance(overview, dict) else {},
            estimates=estimates if isinstance(estimates, dict) else {},
        )
        if not errors:
            return signal
        return CatalystSignal(
            symbol=signal.symbol,
            score_delta=signal.score_delta,
            confidence=signal.confidence,
            provider=signal.provider,
            reasons=signal.reasons,
            risks=[*signal.risks, *errors][:6],
            events=signal.events,
            contributions=signal.contributions,
            news_items=signal.news_items,
            fundamental_snapshot=signal.fundamental_snapshot,
            market_context=signal.market_context,
        )

    def _get_optional(
        self,
        function: str,
        symbol: str,
        errors: list[str],
    ) -> Any:
        try:
            return self._get_cached(function, symbol)
        except AlphaVantageApiError as exc:
            errors.append(f"Alpha Vantage {exc}")
            return {}

    def _get_cached(self, function: str, symbol: str) -> Any:
        cache_key = f"{function}:{symbol}"
        if self.state_store is not None:
            fresh = self.state_store.get_provider_cache(
                self.name,
                cache_key,
                max_age_hours=self.cache_hours,
            )
            if fresh is not None:
                self.state_store.record_provider_call(
                    self.name,
                    function,
                    symbol,
                    True,
                    "cache",
                    item_count=_item_count(fresh[0]),
                    cache_hit=True,
                    message="cache hit",
                )
                return fresh[0]
            if self._calls_today() >= self.daily_call_budget:
                stale = self.state_store.get_provider_cache(self.name, cache_key)
                if stale is not None:
                    self.state_store.record_provider_call(
                        self.name,
                        function,
                        symbol,
                        True,
                        "stale_cache",
                        item_count=_item_count(stale[0]),
                        cache_hit=True,
                        message="daily budget exhausted; stale cache used",
                    )
                    return stale[0]
                raise AlphaVantageApiError(
                    f"{function} daily call budget exhausted and no cache is available"
                )
        payload = self._get(function, symbol)
        if self.state_store is not None:
            self.state_store.set_provider_cache(self.name, cache_key, payload)
        return payload

    def _get(self, function: str, symbol: str) -> dict[str, Any]:
        self._pace_request()
        self.request_count += 1
        try:
            response = requests.get(
                ALPHA_VANTAGE_URL,
                params={
                    "function": function,
                    "symbol": symbol,
                    "apikey": self.api_key,
                },
                headers={"Accept": "application/json", "User-Agent": "stock-analyzer/0.1"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            self._audit(function, symbol, False, type(exc).__name__, 0, "request failed")
            raise AlphaVantageApiError(
                f"{function} request failed: {type(exc).__name__}"
            ) from None
        if response.status_code in {401, 403}:
            self._audit(function, symbol, False, "authorization", 0, "authorization failed")
            raise AlphaVantageApiError(f"{function} authorization failed")
        if response.status_code == 429:
            self._audit(function, symbol, False, "rate_limit", 0, "rate limit reached")
            raise AlphaVantageApiError(f"{function} rate limit reached")
        if not response.ok:
            self._audit(
                function,
                symbol,
                False,
                f"http_{response.status_code}",
                0,
                "request failed",
            )
            raise AlphaVantageApiError(
                f"{function} failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError:
            self._audit(function, symbol, False, "invalid_json", 0, "invalid JSON")
            raise AlphaVantageApiError(f"{function} returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise AlphaVantageApiError(f"{function} returned an unexpected payload")
        if any(key in payload for key in ["Error Message", "Note", "Information"]):
            self._audit(function, symbol, False, "api_limit_or_error", 0, "API error")
            raise AlphaVantageApiError(f"{function} returned an API limit or error message")
        self._audit(function, symbol, True, "ok", _item_count(payload), "ok")
        return payload

    def _pace_request(self) -> None:
        if self._last_request_at is not None:
            elapsed = time_module.monotonic() - self._last_request_at
            remaining = self.min_request_interval_seconds - elapsed
            if remaining > 0:
                time_module.sleep(remaining)
        self._last_request_at = time_module.monotonic()

    def _calls_today(self) -> int:
        if self.state_store is None:
            return self.request_count
        now = datetime.now(timezone.utc)
        start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        return self.state_store.count_provider_calls_since(self.name, start)

    def _audit(
        self,
        function: str,
        symbol: str,
        ok: bool,
        status: str,
        item_count: int,
        message: str,
    ) -> None:
        if self.state_store is not None:
            self.state_store.record_provider_call(
                self.name,
                function,
                symbol,
                ok,
                status,
                item_count=item_count,
                message=message,
            )


def build_alpha_vantage_signal(
    symbol: str,
    run_at: datetime,
    overview: dict[str, Any],
    estimates: dict[str, Any],
) -> CatalystSignal:
    metrics = {
        "revenue_growth_yoy_pct": _to_percent(overview.get("QuarterlyRevenueGrowthYOY")),
        "earnings_growth_yoy_pct": _to_percent(overview.get("QuarterlyEarningsGrowthYOY")),
        "profit_margin_pct": _to_percent(overview.get("ProfitMargin")),
        "forward_pe": _to_float(overview.get("ForwardPE")),
        "analyst_target_price": _to_float(overview.get("AnalystTargetPrice")),
        "analyst_rating_strong_buy": _to_float(overview.get("AnalystRatingStrongBuy")),
        "analyst_rating_buy": _to_float(overview.get("AnalystRatingBuy")),
        "analyst_rating_hold": _to_float(overview.get("AnalystRatingHold")),
        "analyst_rating_sell": _to_float(overview.get("AnalystRatingSell")),
        "analyst_rating_strong_sell": _to_float(overview.get("AnalystRatingStrongSell")),
    }
    contributions: list[SignalContribution] = []
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []

    revenue_growth = metrics["revenue_growth_yoy_pct"]
    earnings_growth = metrics["earnings_growth_yoy_pct"]
    margin = metrics["profit_margin_pct"]
    if isinstance(revenue_growth, float):
        if revenue_growth >= 20:
            contributions.append(_contribution(1.0, f"Revenue growth is {revenue_growth:.1f}%."))
            reasons.append(f"Revenue growth is {revenue_growth:.1f}% year over year.")
        elif revenue_growth <= -10:
            contributions.append(_contribution(-1.0, f"Revenue growth is {revenue_growth:.1f}%."))
            risks.append(f"Revenue declined {abs(revenue_growth):.1f}% year over year.")
    if isinstance(earnings_growth, float):
        if earnings_growth >= 20:
            contributions.append(_contribution(1.0, f"Earnings growth is {earnings_growth:.1f}%."))
            reasons.append(f"Earnings growth is {earnings_growth:.1f}% year over year.")
        elif earnings_growth <= -15:
            contributions.append(_contribution(-1.0, f"Earnings growth is {earnings_growth:.1f}%."))
            risks.append(f"Earnings declined {abs(earnings_growth):.1f}% year over year.")
    if isinstance(margin, float) and margin < 0:
        contributions.append(_contribution(-1.0, f"Profit margin is {margin:.1f}%."))
        risks.append(f"Profit margin is negative at {margin:.1f}%.")

    estimate = _nearest_future_estimate(estimates.get("estimates", []), run_at)
    if estimate:
        current = _to_float(estimate.get("eps_estimate_average"))
        prior = _to_float(estimate.get("eps_estimate_average_30_days_ago"))
        revisions_up = _to_float(estimate.get("eps_estimate_revision_up_trailing_30_days")) or 0
        revisions_down = _to_float(estimate.get("eps_estimate_revision_down_trailing_30_days")) or 0
        if current is not None and prior not in (None, 0):
            change = (current / prior - 1) * 100
            metrics["eps_estimate_change_30d_pct"] = round(change, 2)
            if change >= 3:
                contributions.append(_contribution(1.5, f"EPS estimate rose {change:.1f}% in 30 days."))
                reasons.append(f"EPS estimate rose {change:.1f}% in 30 days.")
            elif change <= -3:
                contributions.append(_contribution(-2.0, f"EPS estimate fell {abs(change):.1f}% in 30 days."))
                risks.append(f"EPS estimate fell {abs(change):.1f}% in 30 days.")
        revision_balance = revisions_up - revisions_down
        metrics["eps_revision_balance_30d"] = revision_balance
        if revision_balance >= 3:
            contributions.append(_contribution(1.0, "Analyst EPS revisions are net positive."))
            reasons.append("Analyst EPS revisions are net positive.")
        elif revision_balance <= -3:
            contributions.append(_contribution(-1.5, "Analyst EPS revisions are net negative."))
            risks.append("Analyst EPS revisions are net negative.")
        events.append(f"Estimate horizon: {estimate.get('date')}")

    target = metrics.get("analyst_target_price")
    if isinstance(target, float):
        events.append(f"Analyst target context: ${target:,.2f}")
    snapshot = FundamentalSnapshot(
        symbol=symbol,
        as_of=run_at,
        provider="alpha_vantage",
        metrics={key: value for key, value in metrics.items() if value is not None},
    )
    return aggregate_signal(
        symbol=symbol,
        provider="alpha_vantage",
        contributions=contributions,
        reasons=reasons,
        risks=risks,
        events=events,
        fundamental_snapshot=snapshot,
    )


def run_alpha_vantage_smoke_test(
    api_key: str,
    symbol: str = "NVDA",
    timeout_seconds: float = 20.0,
) -> list[AlphaVantageEndpointCheck]:
    provider = AlphaVantageCatalystProvider(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    checks: list[AlphaVantageEndpointCheck] = []
    for name, function in [
        ("overview", "OVERVIEW"),
        ("earnings_estimates", "EARNINGS_ESTIMATES"),
    ]:
        try:
            payload = provider._get(function, symbol)
        except AlphaVantageApiError as exc:
            checks.append(AlphaVantageEndpointCheck(name=name, ok=False, message=str(exc)))
            continue
        checks.append(
            AlphaVantageEndpointCheck(
                name=name,
                ok=True,
                item_count=_item_count(payload),
                message="ok",
            )
        )
    return checks


def _nearest_future_estimate(
    estimates: Any,
    run_at: datetime,
) -> dict[str, Any] | None:
    if not isinstance(estimates, list):
        return None
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for item in estimates:
        if not isinstance(item, dict) or item.get("horizon") != "fiscal quarter":
            continue
        try:
            date_value = datetime.fromisoformat(str(item.get("date")))
        except ValueError:
            continue
        if date_value.date() >= run_at.date():
            candidates.append((date_value, item))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _contribution(score: float, summary: str) -> SignalContribution:
    return SignalContribution(
        category="fundamentals_analyst",
        score_delta=score,
        confidence=0.1,
        source="alpha_vantage",
        summary=summary,
        event_id=f"alpha-{summary[:48]}",
    )


def _item_count(payload: Any) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("estimates"), list):
        return len(payload["estimates"])
    return 1 if isinstance(payload, dict) and payload else 0


def _to_percent(value: Any) -> float | None:
    parsed = _to_float(value)
    return parsed * 100 if parsed is not None else None


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
