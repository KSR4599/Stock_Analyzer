from __future__ import annotations

from datetime import datetime

from stock_analyzer.models import StockScore


def format_report(
    scores: list[StockScore],
    run_at: datetime,
    provider: str,
    catalyst_provider: str,
    catalyst_top_n: int,
    universe_source: str,
    universe_size: int,
    budget: float,
    threshold: float,
    top_n: int,
    send_only_alerts: bool = False,
) -> str:
    alerts = [score for score in scores if score.is_alert]
    visible_scores = alerts if send_only_alerts else scores[:top_n]

    lines = [
        f"Moonshot scan - {run_at.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Provider: {provider} | Universe: {universe_size} ({universe_source})",
        f"Catalysts: {catalyst_provider} | Enriched top market names: {catalyst_top_n}",
        f"Trigger: ${budget:.0f} candidate at score >= {threshold:.1f}",
        "",
    ]

    if alerts:
        lines.append(f"ALERTS: {len(alerts)} candidate(s) cleared the threshold.")
    else:
        lines.append("No $250 candidate cleared the threshold this run.")

    lines.append("")

    if not visible_scores:
        lines.append("No ranked scores were available.")
        return "\n".join(lines)

    heading = "Alert candidates" if send_only_alerts else f"Top {min(top_n, len(scores))} ranked names"
    lines.append(heading)

    for index, score in enumerate(visible_scores, start=1):
        amount_text = f"${score.suggested_amount:.0f}" if score.suggested_amount > 0 else "$0"
        score_text = f"{score.score:.1f}"
        if score.market_score is not None and score.catalyst_score:
            score_text = f"{score.score:.1f} (market {score.market_score:.1f}, catalyst {score.catalyst_score:+.1f})"
        lines.extend(
            [
                "",
                f"{index}. {score.symbol} - score {score_text} - {score.action.upper()}",
                f"   Setup: {score.setup} | Risk: {score.risk_level}",
                f"   Last price: ${score.last_price:.2f} | Suggested amount: {amount_text}",
                f"   Why: {' '.join(score.reasons[:3])}",
                f"   Watchouts: {' '.join(score.risks[:2])}",
            ]
        )
        if score.catalysts:
            lines.append(f"   Catalysts: {' | '.join(score.catalysts[:2])}")

    lines.extend(
        [
            "",
            "Note: research-only signal. No automatic trading is enabled.",
        ]
    )
    return "\n".join(lines)
