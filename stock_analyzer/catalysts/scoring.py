from __future__ import annotations

from dataclasses import replace

import numpy as np

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.models import StockScore


def apply_catalyst_signals(
    scores: list[StockScore],
    signals: dict[str, CatalystSignal],
    alert_threshold: float,
    budget: float,
) -> list[StockScore]:
    enriched = [_apply_signal(score, signals.get(score.symbol), alert_threshold, budget) for score in scores]
    return sorted(enriched, key=lambda item: item.score, reverse=True)


def _apply_signal(
    score: StockScore,
    signal: CatalystSignal | None,
    alert_threshold: float,
    budget: float,
) -> StockScore:
    if signal is None:
        return replace(
            score,
            market_score=score.market_score if score.market_score is not None else score.score,
            catalyst_score=0.0,
        )

    market_score = score.market_score if score.market_score is not None else score.score
    catalyst_delta = float(np.clip(signal.score_delta, -25, 25))
    final_score = round(float(np.clip(market_score + catalyst_delta, 0, 100)), 1)
    action = _action_after_catalyst(score.action, market_score, final_score, alert_threshold, score.risk_level)
    suggested_amount = float(budget) if action == "candidate" else 0.0
    setup = score.setup
    if signal.score_delta >= 4 and "catalyst" not in setup:
        setup = f"{setup} + catalyst"

    metrics = {
        **score.metrics,
        "market_score": round(market_score, 1),
        "catalyst_score": round(catalyst_delta, 1),
        "catalyst_confidence": round(signal.confidence, 2),
    }

    if signal.score_delta == 0 and not signal.events:
        reasons = score.reasons
        risks = _dedupe([*signal.risks, *score.risks])
    else:
        reasons = [*signal.reasons[:3], *score.reasons]
        risks = [*signal.risks[:3], *score.risks]
        risks = [
            risk
            for risk in risks
            if risk != "Signal uses market data only; fundamentals/news/earnings catalysts are not in this pass."
        ]
        risks.append("Catalyst score is heuristic; verify source articles, filings, and earnings details before acting.")

    return replace(
        score,
        score=final_score,
        action=action,
        suggested_amount=suggested_amount,
        setup=setup,
        market_score=round(market_score, 1),
        catalyst_score=round(catalyst_delta, 1),
        catalyst_provider=signal.provider,
        catalysts=signal.events[:5],
        metrics=metrics,
        reasons=_dedupe(reasons),
        risks=_dedupe(risks),
    )


def _action_after_catalyst(
    original_action: str,
    market_score: float,
    final_score: float,
    alert_threshold: float,
    risk_level: str,
) -> str:
    if final_score < alert_threshold - 10:
        return "skip"
    if final_score < alert_threshold:
        return "watch"
    if original_action == "candidate":
        return "candidate"
    if original_action == "watch" and market_score >= alert_threshold - 12 and risk_level != "speculative":
        return "candidate"
    return "watch"


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped
