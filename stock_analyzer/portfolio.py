from __future__ import annotations

from datetime import datetime

import pandas as pd

from stock_analyzer.catalysts.base import CatalystSignal
from stock_analyzer.models import StockScore
from stock_analyzer.portfolio_models import (
    PORTFOLIO_ACTIONS,
    PortfolioAssessment,
    PortfolioPolicy,
    PortfolioPosition,
)


CLASSIFICATION_LABELS = {
    "core_etf": "core ETF",
    "thematic_etf": "thematic ETF",
    "established": "established",
    "growth_cyclical": "growth/cyclical",
    "speculative": "speculative",
}
LOSS_THRESHOLDS = {
    "core_etf": -12.0,
    "thematic_etf": -18.0,
    "established": -15.0,
    "growth_cyclical": -20.0,
    "speculative": -25.0,
}
ACTION_PRIORITY = {
    "EXIT REVIEW": 0,
    "TRIM REVIEW": 1,
    "BUY-MORE REVIEW": 2,
    "WATCH": 3,
    "HOLD": 4,
}
ACTION_EMOJIS = {
    "EXIT REVIEW": "🔴",
    "TRIM REVIEW": "🟠",
    "BUY-MORE REVIEW": "🔵",
    "WATCH": "🟡",
    "HOLD": "🟢",
}
CRITICAL_RISK_TERMS = {
    "delisting",
    "bankruptcy",
    "going concern",
    "material weakness",
}
CAUTIONARY_RISK_TERMS = {
    "dilution",
    "offering",
    "financing risk",
    "sec 144",
    "share sales",
    "insider sales",
    "shares outstanding increased",
}


def classify_position(
    position: PortfolioPosition,
    score: StockScore,
    policy: PortfolioPolicy,
) -> str:
    if policy.classification_override not in {None, "adaptive"}:
        return policy.classification_override
    if position.classification != "adaptive":
        return position.classification
    volatility = _metric(score, "volatility_20d_annualized_pct", 999.0)
    atr = _metric(score, "atr_14d_pct", 999.0)
    if volatility <= 50 and atr <= 5:
        return "established"
    if volatility <= 100 and atr <= 10:
        return "growth_cyclical"
    return "speculative"


def assess_position(
    position: PortfolioPosition,
    score: StockScore,
    history: pd.DataFrame,
    policy: PortfolioPolicy,
    weight_pct: float,
    degraded: bool,
    shadow_context: CatalystSignal | None = None,
) -> PortfolioAssessment:
    classification = classify_position(position, score, policy)
    current_price = portfolio_market_price(score, history)
    current_value = position.quantity * current_price
    return_from_cost = (
        (current_price / position.average_cost - 1) * 100
        if position.average_cost > 0
        else 0.0
    )
    daily_return = _daily_return(history)
    return_5d = _metric_optional(score, "return_5d_pct")
    atr = _metric(score, "atr_14d_pct", 0.0)
    distance_ema50 = _metric(score, "distance_from_ema_50_pct", 0.0)
    distance_ema21 = _metric(score, "distance_from_ema_21_pct", 0.0)
    relative_21d = _metric(score, "relative_to_spy_21d_pct", 0.0)
    volatility = _metric(score, "volatility_20d_annualized_pct", 0.0)
    loss_threshold = LOSS_THRESHOLDS[classification]
    deterministic_risks = [*score.risks]
    critical_risk = any(
        term in risk.lower()
        for risk in deterministic_risks
        for term in CRITICAL_RISK_TERMS
    )
    cautionary_risk = any(
        term in risk.lower()
        for risk in deterministic_risks
        for term in CAUTIONARY_RISK_TERMS
    )
    upcoming_earnings = any(
        "upcoming earnings" in risk.lower() for risk in deterministic_risks
    )
    technical_breakdown = distance_ema50 < -5 or relative_21d < -8
    acute_drop = daily_return is not None and daily_return <= -max(5.0, 1.5 * atr)
    rapid_drop = return_5d is not None and return_5d <= -max(8.0, 2.0 * atr)
    momentum_overextension = (
        return_5d is not None
        and return_5d >= max(15.0, 2.0 * atr)
    ) or (
        return_from_cost >= 30
        and (volatility >= 85 or atr >= 7)
    )
    overextended_reversal = (
        return_from_cost >= 40
        and (distance_ema21 >= 20 or volatility >= 120)
        and (daily_return or 0) < 0
    )

    reasons = [
        f"Position return from average cost is {return_from_cost:+.1f}%.",
        f"Portfolio weight is {weight_pct:.1f}%.",
        f"Deterministic market score is {score.score:.1f}.",
    ]
    risks: list[str] = []
    if degraded:
        action = "WATCH"
        risks.append("Market-data coverage is degraded; actionable reviews are suppressed.")
    elif return_from_cost <= loss_threshold and technical_breakdown:
        action = "EXIT REVIEW"
        risks.append(
            f"Loss crossed the {abs(loss_threshold):.0f}% {CLASSIFICATION_LABELS[classification]} threshold with technical deterioration."
        )
    elif critical_risk and technical_breakdown:
        action = "EXIT REVIEW"
        risks.append("Critical deterministic risk is confirmed by technical deterioration.")
    elif not policy.concentration_exempt and weight_pct >= 25:
        action = "TRIM REVIEW"
        risks.append("Single-position weight is at or above 25%.")
    elif overextended_reversal:
        action = "TRIM REVIEW"
        risks.append("Large unrealized gain is paired with overextension and reversal risk.")
    elif (
        policy.buy_more_enabled
        and score.score >= 78
        and score.action == "candidate"
        and distance_ema50 >= 0
        and relative_21d > 0
        and volatility <= 85
        and atr <= 7
        and (return_5d is None or return_5d <= 15)
        and return_from_cost <= 30
        and not critical_risk
        and not cautionary_risk
        and not upcoming_earnings
        and not (
            return_from_cost >= 40
            and (distance_ema21 >= 20 or volatility >= 100)
        )
        and weight_pct < 10
    ):
        action = "BUY-MORE REVIEW"
        reasons.append("Market gates pass without severe deterministic risk.")
    elif (
        acute_drop
        or rapid_drop
        or technical_breakdown
        or critical_risk
        or cautionary_risk
        or momentum_overextension
        or return_from_cost <= loss_threshold / 2
    ):
        action = "WATCH"
        if acute_drop:
            risks.append("One-day decline exceeded the volatility-adjusted warning level.")
        if rapid_drop:
            risks.append("Five-day decline exceeded the volatility-adjusted warning level.")
        if technical_breakdown:
            risks.append("Price trend or SPY-relative strength has deteriorated.")
        if critical_risk:
            risks.append("Recent deterministic evidence contains a critical risk signal.")
        if cautionary_risk:
            risks.append("Recent deterministic evidence contains a cautionary filing signal.")
        if momentum_overextension:
            risks.append(
                "Recent gains or volatility are too extended for a buy-more review."
            )
    else:
        action = "HOLD"

    if shadow_context is not None and shadow_context.risks:
        risks.extend(f"Shadow context: {risk}" for risk in shadow_context.risks[:2])
    return PortfolioAssessment(
        symbol=position.symbol,
        action=action,
        classification=classification,
        current_price=round(current_price, 4),
        current_value=round(current_value, 2),
        weight_pct=round(weight_pct, 2),
        return_from_cost_pct=round(return_from_cost, 2),
        daily_return_pct=round(daily_return, 2) if daily_return is not None else None,
        return_5d_pct=round(return_5d, 2) if return_5d is not None else None,
        score=score.score,
        reasons=_dedupe(reasons),
        risks=_dedupe([*risks, *score.risks[:2]])[:5],
    )


def portfolio_market_price(score: StockScore, history: pd.DataFrame) -> float:
    if score.last_price > 0:
        return score.last_price
    if history.empty:
        return 0.0
    frame = history.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "close" not in frame.columns:
        return 0.0
    closes = pd.to_numeric(frame["close"], errors="coerce").dropna()
    return float(closes.iloc[-1]) if not closes.empty else 0.0


def format_portfolio_report(
    run_at: datetime,
    positions: dict[str, PortfolioPosition],
    assessments: list[PortfolioAssessment],
    coverage_pct: float,
    degraded: bool,
    previous_actions: dict[str, str] | None = None,
) -> str:
    total_value = sum(item.current_value for item in assessments)
    total_cost = sum(
        positions[item.symbol].quantity * positions[item.symbol].average_cost
        for item in assessments
    )
    total_return = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0.0
    counts: dict[str, int] = {}
    for item in assessments:
        counts[item.action] = counts.get(item.action, 0) + 1
    count_text = "  ".join(
        f"{ACTION_EMOJIS[action]} {action.replace(' REVIEW', '')}: {counts[action]}"
        for action in [
            "EXIT REVIEW",
            "TRIM REVIEW",
            "BUY-MORE REVIEW",
            "WATCH",
            "HOLD",
        ]
        if counts.get(action)
    )
    health_emoji = "⚠️" if degraded else "✅"
    lines = [
        "📊 PORTFOLIO PULSE",
        f"🕒 {run_at.strftime('%a, %b %d · %I:%M %p %Z')}",
        "",
        f"{health_emoji} Data health: {coverage_pct:.1f}% coverage"
        + (" · ACTIONS SUPPRESSED" if degraded else " · Healthy"),
        f"💰 Market value: ${total_value:,.2f}",
        f"📈 Return vs. average cost: {total_return:+.2f}%",
        "",
        "🚦 ACTION SUMMARY",
        count_text or "No positions assessed.",
    ]
    previous_actions = previous_actions or {}
    transitions = [
        (item.symbol, previous_actions[item.symbol], item.action)
        for item in assessments
        if item.symbol in previous_actions
        and previous_actions[item.symbol] != item.action
    ]
    if previous_actions:
        lines.extend(["", "🔄 CHANGES SINCE LAST REVIEW"])
        if transitions:
            for symbol, old_action, new_action in sorted(transitions):
                lines.append(
                    f"• {symbol}: {ACTION_EMOJIS[old_action]} "
                    f"{old_action.replace(' REVIEW', '')} → "
                    f"{ACTION_EMOJIS[new_action]} "
                    f"{new_action.replace(' REVIEW', '')}"
                )
        else:
            lines.append("• No action-label changes.")

    ordered = sorted(
        assessments,
        key=lambda item: (ACTION_PRIORITY[item.action], -item.weight_pct, item.symbol),
    )
    priority = [item for item in ordered if item.action in {
        "EXIT REVIEW",
        "TRIM REVIEW",
        "BUY-MORE REVIEW",
    }]
    if priority:
        lines.extend(["", "⚡ PRIORITY REVIEWS"])
    for item in priority:
        position = positions[item.symbol]
        daily = (
            f"{item.daily_return_pct:+.2f}%"
            if item.daily_return_pct is not None
            else "n/a"
        )
        lines.extend(
            [
                "",
                f"{ACTION_EMOJIS[item.action]} {item.symbol} · {item.action}",
                f"💵 ${item.current_price:,.2f}  |  Avg ${position.average_cost:,.2f}"
                f"  |  P/L {item.return_from_cost_pct:+.2f}%",
                f"📦 {position.quantity:g} shares  |  ${item.current_value:,.2f}"
                f" value  |  {item.weight_pct:.2f}% weight",
                f"📉 Day {daily}  |  Score {item.score:.1f}"
                f"  |  {CLASSIFICATION_LABELS[item.classification]}",
            ]
        )
        if item.risks:
            lines.append(f"⚠️ {_compact_sentence(item.risks[0])}")
        previous_action = previous_actions.get(item.symbol)
        if previous_action and previous_action != item.action:
            lines.append(
                f"↪️ Previous: {ACTION_EMOJIS[previous_action]} {previous_action}"
            )

    lines.extend(["", "📋 COMPLETE PORTFOLIO"])
    for action in [
        "EXIT REVIEW",
        "TRIM REVIEW",
        "BUY-MORE REVIEW",
        "WATCH",
        "HOLD",
    ]:
        group = [item for item in ordered if item.action == action]
        if not group:
            continue
        lines.extend(
            [
                "",
                f"{ACTION_EMOJIS[action]} {action} · {len(group)}",
            ]
        )
        for item in group:
            position = positions[item.symbol]
            daily = (
                f"{item.daily_return_pct:+.1f}%"
                if item.daily_return_pct is not None
                else "n/a"
            )
            lines.append(
                f"• {item.symbol}  ${item.current_price:,.2f}"
                f"  |  P/L {item.return_from_cost_pct:+.1f}%"
                f"  |  Day {daily}"
                f"  |  Wt {item.weight_pct:.1f}%"
            )
            lines.append(
                f"  Qty {position.quantity:g}  ·  Avg ${position.average_cost:,.2f}"
                f"  ·  Score {item.score:.0f}"
            )

    lines.extend(
        [
            "",
            "ℹ️ Research-only review · No automatic trades or tax advice.",
        ]
    )
    return "\n".join(lines)


def _daily_return(history: pd.DataFrame) -> float | None:
    if history.empty:
        return None
    frame = history.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "close" not in frame.columns:
        return None
    closes = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(closes) < 2 or closes.iloc[-2] <= 0:
        return None
    return float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100)


def _metric(score: StockScore, name: str, default: float) -> float:
    value = score.metrics.get(name)
    return float(value) if isinstance(value, (int, float)) else default


def _metric_optional(score: StockScore, name: str) -> float | None:
    value = score.metrics.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _compact_sentence(value: str, max_length: int = 150) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"
