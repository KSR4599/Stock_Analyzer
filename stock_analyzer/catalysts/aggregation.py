from __future__ import annotations

from dataclasses import replace

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.catalysts.models import (
    FundamentalSnapshot,
    MarketContext,
    NewsItem,
    SignalContribution,
)


CATEGORY_CAPS: dict[str, tuple[float, float]] = {
    "news": (-8.0, 6.0),
    "earnings": (-6.0, 4.0),
    "filings_insiders": (-8.0, 5.0),
    "fundamentals_analyst": (-4.0, 4.0),
    "macro": (-5.0, 0.0),
}
TOTAL_CAP = (-15.0, 10.0)


def aggregate_signal(
    symbol: str,
    provider: str,
    contributions: list[SignalContribution],
    reasons: list[str] | None = None,
    risks: list[str] | None = None,
    events: list[str] | None = None,
    news_items: list[NewsItem] | None = None,
    fundamental_snapshot: FundamentalSnapshot | None = None,
    market_context: MarketContext | None = None,
) -> CatalystSignal:
    deduped = _dedupe_contributions(contributions)
    capped = _apply_category_caps(deduped)
    capped = _apply_total_cap(capped)
    score = sum(item.score_delta for item in capped)
    confidence = min(sum(item.confidence for item in capped), 1.0)
    return CatalystSignal(
        symbol=symbol,
        score_delta=round(score, 1),
        confidence=round(confidence, 2),
        provider=provider,
        reasons=_dedupe(reasons or [])[:6],
        risks=_dedupe(risks or [])[:6],
        events=_dedupe(events or [])[:8],
        contributions=capped,
        news_items=news_items or [],
        fundamental_snapshot=fundamental_snapshot,
        market_context=market_context,
    )


def _dedupe_contributions(
    contributions: list[SignalContribution],
) -> list[SignalContribution]:
    without_id: list[SignalContribution] = []
    grouped: dict[tuple[str, str], list[SignalContribution]] = {}
    for item in contributions:
        if not item.event_id:
            without_id.append(item)
            continue
        grouped.setdefault((item.category, item.event_id), []).append(item)

    deduped = list(without_id)
    for items in grouped.values():
        strongest = max(items, key=lambda item: abs(item.score_delta))
        sources = sorted({item.source for item in items})
        confidence = min(
            max(item.confidence for item in items) + max(0, len(sources) - 1) * 0.05,
            1.0,
        )
        metadata = {
            **strongest.metadata,
            "corroborating_sources": sources,
        }
        deduped.append(
            replace(
                strongest,
                confidence=round(confidence, 3),
                metadata=metadata,
            )
        )
    return deduped


def _apply_category_caps(
    contributions: list[SignalContribution],
) -> list[SignalContribution]:
    result: list[SignalContribution] = []
    grouped: dict[str, list[SignalContribution]] = {}
    for item in contributions:
        grouped.setdefault(item.category, []).append(item)

    for category, items in grouped.items():
        lower, upper = CATEGORY_CAPS.get(category, (-15.0, 10.0))
        result.extend(_cap_group(items, lower, upper))
    return result


def _apply_total_cap(
    contributions: list[SignalContribution],
) -> list[SignalContribution]:
    total = sum(item.score_delta for item in contributions)
    lower, upper = TOTAL_CAP
    if lower <= total <= upper:
        return contributions

    positives = sum(max(item.score_delta, 0.0) for item in contributions)
    negatives = sum(min(item.score_delta, 0.0) for item in contributions)
    if total > upper and positives > 0:
        target_positive = max(0.0, upper - negatives)
        factor = target_positive / positives
        return [
            replace(item, score_delta=round(item.score_delta * factor, 3))
            if item.score_delta > 0
            else item
            for item in contributions
        ]
    if total < lower and negatives < 0:
        target_negative = min(0.0, lower - positives)
        factor = target_negative / negatives
        return [
            replace(item, score_delta=round(item.score_delta * factor, 3))
            if item.score_delta < 0
            else item
            for item in contributions
        ]
    return contributions


def _cap_group(
    items: list[SignalContribution],
    lower: float,
    upper: float,
) -> list[SignalContribution]:
    total = sum(item.score_delta for item in items)
    if lower <= total <= upper:
        return items

    positives = sum(max(item.score_delta, 0.0) for item in items)
    negatives = sum(min(item.score_delta, 0.0) for item in items)
    if total > upper and positives > 0:
        target_positive = max(0.0, upper - negatives)
        factor = target_positive / positives
        return [
            replace(item, score_delta=round(item.score_delta * factor, 3))
            if item.score_delta > 0
            else item
            for item in items
        ]
    if total < lower and negatives < 0:
        target_negative = min(0.0, lower - positives)
        factor = target_negative / negatives
        return [
            replace(item, score_delta=round(item.score_delta * factor, 3))
            if item.score_delta < 0
            else item
            for item in items
        ]
    return items


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
