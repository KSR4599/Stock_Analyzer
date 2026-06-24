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
    report_kind: str = "scheduled_report",
    market_requested: int | None = None,
    market_received: int | None = None,
    market_coverage_pct: float | None = None,
    market_degraded: bool = False,
    market_failures: list[str] | None = None,
) -> str:
    alerts = [score for score in scores if score.is_alert]
    visible_scores = alerts if send_only_alerts else scores[:top_n]

    lines = [
        f"{_report_title(report_kind)} - {run_at.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Provider: {provider} | Universe: {universe_size} ({universe_source})",
        f"Catalysts: {catalyst_provider} | Enriched top market names: {catalyst_top_n}",
        f"Trigger: ${budget:.0f} candidate at score >= {threshold:.1f}",
        "",
    ]
    if market_requested is not None and market_received is not None:
        coverage = market_coverage_pct if market_coverage_pct is not None else 0.0
        state = "DEGRADED" if market_degraded else "healthy"
        lines.insert(
            2,
            f"Market data: {market_received}/{market_requested} "
            f"({coverage:.1f}%) | {state}",
        )
    if market_degraded:
        failed = ", ".join((market_failures or [])[:10])
        lines.extend(
            [
                "WARNING: Candidate alerts were suppressed because market data was incomplete.",
                f"Missing symbols: {failed or 'benchmark or usable scores unavailable'}",
                "",
            ]
        )

    if alerts:
        lines.append(f"ALERTS: {len(alerts)} candidate(s) cleared the threshold.")
    else:
        lines.append(f"No ${budget:.0f} candidate cleared the threshold this run.")

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
                f"   Change: {_change_text(score)}",
                f"   Calibration: {_calibration_text(score)}",
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


def _change_text(score: StockScore) -> str:
    state = str(score.metrics.get("signal_state", "new_coverage")).replace("_", " ")
    score_delta = score.metrics.get("score_delta")
    rank_delta = score.metrics.get("rank_delta")
    if state == "steady" and score_delta == 0 and rank_delta == 0:
        return "steady vs prior comparable run"
    parts = [state]
    if isinstance(score_delta, (int, float)):
        parts.append(f"score {score_delta:+.1f}")
    if isinstance(rank_delta, int):
        parts.append(f"rank {rank_delta:+d}")
    new_reasons = score.metrics.get("new_reasons")
    if isinstance(new_reasons, list) and new_reasons:
        parts.append(f"{len(new_reasons)} new insight(s)")
    return " | ".join(parts)


def _calibration_text(score: StockScore) -> str:
    sample_count = score.metrics.get("calibration_sample_count")
    confidence = score.metrics.get("calibration_confidence", "unmeasured")
    horizon = score.metrics.get("calibration_horizon_days", 3)
    band = score.metrics.get("calibration_score_band", "unknown")
    if not isinstance(sample_count, int) or sample_count <= 0:
        return f"{confidence} episode-adjusted evidence for {horizon}d {band} band"
    win_rate = score.metrics.get("calibration_win_rate_pct")
    median = score.metrics.get("calibration_median_return_pct")
    pieces = [
        f"{confidence}: n={sample_count} episodes",
        f"{horizon}d {band} band",
    ]
    if isinstance(win_rate, (int, float)):
        pieces.append(f"win {win_rate:.1f}%")
    if isinstance(median, (int, float)):
        pieces.append(f"median {median:+.2f}%")
    return " | ".join(pieces)


def format_error_alert(error: Exception, run_at: datetime) -> str:
    error_type = type(error).__name__
    return "\n".join(
        [
            f"Stock Analyzer error alert - {run_at.strftime('%Y-%m-%d %H:%M %Z')}",
            f"Error type: {error_type}",
            "A scheduled scan failed before completion. Check the local service logs for details.",
            "",
            "Note: secrets are intentionally omitted from this alert.",
        ]
    )


def _report_title(report_kind: str) -> str:
    titles = {
        "scheduled_report": "Stock Analyzer scheduled report",
        "candidate_alert": "Stock Analyzer candidate alert",
    }
    return titles.get(report_kind, "Stock Analyzer report")
