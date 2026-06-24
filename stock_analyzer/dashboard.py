from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, render_template, request
from werkzeug.serving import make_server

from stock_analyzer.database import shadow_promotion_gate, shadow_provider_summaries
from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS


THREE_HOUR_HEALTHY = 4.5
THREE_HOUR_WARNING = 7.5
SHADOW_HEALTHY = 12.0
SHADOW_WARNING = 20.0
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
PACIFIC = ZoneInfo("America/Los_Angeles")
MARKET_OPEN_PT = time(6, 30)
MARKET_CLOSE_PT = time(13, 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_regular_market_hours(now: datetime | None = None) -> bool:
    local = (now or _now()).astimezone(PACIFIC)
    return (
        local.weekday() < 5
        and MARKET_OPEN_PT <= local.time() <= MARKET_CLOSE_PT
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(
    value: str | None,
    healthy_hours: float,
    warning_hours: float,
) -> dict[str, Any]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return {"status": "warning", "age_hours": None, "label": "No data"}
    age = max(0.0, (_now() - parsed).total_seconds() / 3600)
    status = "healthy" if age <= healthy_hours else "warning"
    qualifier = "stale" if age > warning_hours else "due soon"
    return {
        "status": status,
        "age_hours": round(age, 2),
        "label": f"{age:.1f}h ago" + (f" - {qualifier}" if status == "warning" else ""),
    }


def _json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _split_text(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(" | ") if item.strip()]


def _median(values: Iterable[float]) -> float | None:
    clean = list(values)
    return round(statistics.median(clean), 2) if clean else None


def _source_label(is_shadow: bool) -> str:
    return "Shadow Context" if is_shadow else "Production SEC"


class DashboardStore:
    """Read-only dashboard queries over the analyzer SQLite audit database."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def connect(self) -> sqlite3.Connection:
        encoded = quote(str(self.path), safe="/")
        connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def overview(self) -> dict[str, Any]:
        portfolio = self.portfolio()
        production = self.ideas("production", limit=8)
        shadow = self.ideas("shadow", limit=8)
        health = self.health()
        changes: list[dict[str, str]] = []

        for item in portfolio["changes"][:8]:
            changes.append(
                {
                    "kind": "portfolio",
                    "title": item["symbol"],
                    "detail": f"{item['previous_action']} → {item['action']}",
                }
            )
        for item in production["ideas"][:8]:
            if item["signal_state"] != "steady":
                score_delta = item["score_delta"]
                delta_text = (
                    f"{float(score_delta):+.1f}"
                    if score_delta is not None
                    else "new"
                )
                changes.append(
                    {
                        "kind": "candidate",
                        "title": item["symbol"],
                        "detail": (
                            f"{item['signal_state'].replace('_', ' ')} · "
                            f"score {item['score']:.1f} "
                            f"({delta_text})"
                        ),
                    }
                )
        for service in health["services"]:
            if service["status"] != "healthy":
                changes.append(
                    {
                        "kind": "health",
                        "title": service["name"],
                        "detail": service["detail"],
                    }
                )

        statuses = [item["status"] for item in health["services"]]
        production_by_symbol = {item["symbol"]: item for item in production["ideas"]}
        shadow_by_symbol = {item["symbol"]: item for item in shadow["ideas"]}
        shared_symbols = sorted(set(production_by_symbol) & set(shadow_by_symbol))
        agreement_rows = []
        for symbol in shared_symbols:
            prod = production_by_symbol[symbol]
            experimental = shadow_by_symbol[symbol]
            gap = round(float(experimental["score"]) - float(prod["score"]), 1)
            agreement_rows.append(
                {
                    "symbol": symbol,
                    "production_score": prod["score"],
                    "shadow_score": experimental["score"],
                    "score_gap": gap,
                    "production_action": prod["action"],
                    "shadow_action": experimental["action"],
                    "aligned": prod["action"] == experimental["action"],
                }
            )
        agreement_rows.sort(key=lambda item: abs(item["score_gap"]), reverse=True)
        movers = sorted(
            [
                item
                for item in production["ideas"]
                if item["score_delta"] is not None
                and (
                    abs(float(item["score_delta"])) >= 0.1
                    or item["signal_state"] != "steady"
                )
            ],
            key=lambda item: abs(item["score_delta"]),
            reverse=True,
        )[:10]
        pulse = {
            "new_candidates": sum(
                item["signal_state"] == "new_candidate"
                for item in production["ideas"]
            ),
            "lost_candidates": sum(
                item["signal_state"] == "lost_candidate"
                for item in production["ideas"]
            ),
            "upgrades": sum(
                item["signal_state"] == "upgraded"
                for item in production["ideas"]
            ),
            "downgrades": sum(
                item["signal_state"] == "downgraded"
                for item in production["ideas"]
            ),
            "fresh_insights": sum(
                len(item["new_reasons"]) + len(item["new_risks"])
                for item in production["ideas"]
            ),
        }
        return {
            "as_of": _now().isoformat(),
            "source": "decision_cockpit",
            "freshness": portfolio["freshness"],
            "degraded": "critical" in statuses,
            "sample_count": portfolio["sample_count"],
            "portfolio": portfolio["summary"],
            "allocation": portfolio["allocation"],
            "production": production,
            "shadow": shadow,
            "changes": changes[:16],
            "pulse": pulse,
            "movers": movers,
            "agreement": {
                "sample_count": len(agreement_rows),
                "aligned_count": sum(item["aligned"] for item in agreement_rows),
                "rows": agreement_rows[:10],
            },
            "health": health,
        }

    def portfolio(self) -> dict[str, Any]:
        with self.connect() as connection:
            latest = connection.execute(
                """
                SELECT id, import_id, started_at, market_coverage_pct,
                       market_degraded, total_invested_value,
                       analysis_status, notification_status,
                       notification_message, completed_at
                FROM portfolio_monitor_runs
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if latest is None:
                return {
                    "as_of": _now().isoformat(),
                    "source": "portfolio",
                    "freshness": _freshness(None, THREE_HOUR_HEALTHY, THREE_HOUR_WARNING),
                    "degraded": True,
                    "sample_count": 0,
                    "summary": {},
                    "positions": [],
                    "allocation": [],
                    "history": [],
                    "changes": [],
                }
            previous = connection.execute(
                """
                SELECT id FROM portfolio_monitor_runs
                WHERE import_id = ? AND id < ?
                  AND market_degraded = 0 AND market_coverage_pct >= 90
                ORDER BY id DESC LIMIT 1
                """,
                (latest["import_id"], latest["id"]),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT a.symbol, a.action, a.classification, a.current_price,
                       a.current_value, a.weight_pct, a.return_from_cost_pct,
                       a.daily_return_pct, a.return_5d_pct, a.score,
                       a.reasons_text, a.risks_text, p.quantity, p.average_cost,
                       pol.concentration_exempt, pol.buy_more_enabled
                FROM portfolio_assessments a
                JOIN portfolio_positions p
                  ON p.import_id = ? AND p.symbol = a.symbol
                LEFT JOIN portfolio_policies pol ON pol.symbol = a.symbol
                WHERE a.run_id = ?
                ORDER BY a.weight_pct DESC, a.symbol
                """,
                (latest["import_id"], latest["id"]),
            ).fetchall()
            prior_actions: dict[str, str] = {}
            if previous:
                prior_actions = {
                    row["symbol"]: row["action"]
                    for row in connection.execute(
                        """
                        SELECT symbol, action FROM portfolio_assessments
                        WHERE run_id = ?
                        """,
                        (previous["id"],),
                    ).fetchall()
                }
            history_rows = connection.execute(
                """
                SELECT r.id, r.started_at, r.total_invested_value,
                       r.market_coverage_pct, r.market_degraded,
                       SUM(p.quantity * p.average_cost) AS total_cost
                FROM portfolio_monitor_runs r
                JOIN portfolio_positions p ON p.import_id = r.import_id
                GROUP BY r.id
                ORDER BY r.id DESC LIMIT 40
                """
            ).fetchall()
            action_rows = connection.execute(
                """
                SELECT run_id, action, COUNT(*) AS count
                FROM portfolio_assessments
                WHERE run_id IN (
                    SELECT id FROM portfolio_monitor_runs ORDER BY id DESC LIMIT 40
                )
                GROUP BY run_id, action
                """
            ).fetchall()
            stability_rows = connection.execute(
                """
                SELECT r.id AS run_id, r.started_at, r.market_degraded,
                       r.market_coverage_pct, a.symbol, a.action
                FROM portfolio_monitor_runs r
                JOIN portfolio_assessments a ON a.run_id = r.id
                WHERE r.import_id = ?
                ORDER BY r.id DESC LIMIT 800
                """,
                (latest["import_id"],),
            ).fetchall()

        streaks = _portfolio_streaks(stability_rows)
        positions = []
        changes = []
        total_cost = 0.0
        for row in rows:
            total_cost += float(row["quantity"]) * float(row["average_cost"])
            previous_action = prior_actions.get(row["symbol"])
            item = {
                "symbol": row["symbol"],
                "action": row["action"],
                "classification": row["classification"],
                "quantity": row["quantity"],
                "average_cost": row["average_cost"],
                "price": row["current_price"],
                "value": row["current_value"],
                "allocation_pct": row["weight_pct"],
                "pl_pct": row["return_from_cost_pct"],
                "daily_return_pct": row["daily_return_pct"],
                "return_5d_pct": row["return_5d_pct"],
                "score": row["score"],
                "reasons": _split_text(row["reasons_text"]),
                "risks": _split_text(row["risks_text"]),
                "previous_action": previous_action,
                "action_streak": streaks.get(row["symbol"], {}).get("streak", 1),
                "action_transitions": streaks.get(row["symbol"], {}).get("transitions", 0),
                "concentration_exempt": bool(row["concentration_exempt"]),
                "buy_more_enabled": (
                    True if row["buy_more_enabled"] is None else bool(row["buy_more_enabled"])
                ),
            }
            positions.append(item)
            if previous_action and previous_action != row["action"]:
                changes.append(item)

        actions: dict[str, int] = defaultdict(int)
        for item in positions:
            actions[item["action"]] += 1
        total_value = float(latest["total_invested_value"])
        total_return = (total_value / total_cost - 1) * 100 if total_cost else 0.0
        action_history: dict[int, dict[str, int]] = defaultdict(dict)
        for row in action_rows:
            action_history[int(row["run_id"])][row["action"]] = row["count"]
        history = []
        for row in reversed(history_rows):
            cost = float(row["total_cost"] or 0)
            value = float(row["total_invested_value"])
            history.append(
                {
                    "run_id": row["id"],
                    "started_at": row["started_at"],
                    "value": round(value, 2),
                    "return_pct": round((value / cost - 1) * 100, 2) if cost else 0.0,
                    "coverage_pct": row["market_coverage_pct"],
                    "degraded": bool(row["market_degraded"]),
                    "actions": action_history.get(int(row["id"]), {}),
                }
            )
        allocation = _allocation(positions)
        freshness = _freshness(
            latest["started_at"], THREE_HOUR_HEALTHY, THREE_HOUR_WARNING
        )
        return {
            "as_of": latest["started_at"],
            "source": "portfolio",
            "freshness": freshness,
            "degraded": bool(latest["market_degraded"]),
            "sample_count": len(positions),
            "summary": {
                "run_id": latest["id"],
                "market_value": round(total_value, 2),
                "total_cost": round(total_cost, 2),
                "return_pct": round(total_return, 2),
                "coverage_pct": latest["market_coverage_pct"],
                "degraded": bool(latest["market_degraded"]),
                "actions": dict(actions),
                "analysis_status": latest["analysis_status"],
                "notification_status": latest["notification_status"],
                "notification_message": latest["notification_message"],
                "freshness": freshness,
            },
            "positions": positions,
            "allocation": allocation,
            "history": history,
            "changes": changes,
        }

    def ideas(self, source: str, limit: int = 30) -> dict[str, Any]:
        is_shadow = source == "shadow"
        if source not in {"production", "shadow"}:
            raise ValueError("source must be production or shadow")
        with self.connect() as connection:
            run = connection.execute(
                """
                SELECT r.id, r.started_at, r.market_coverage_pct,
                       r.market_degraded, c.catalyst_provider
                FROM runs r
                JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = ?
                  AND EXISTS (SELECT 1 FROM scores s WHERE s.run_id = r.id)
                ORDER BY r.id DESC LIMIT 1
                """,
                (int(is_shadow),),
            ).fetchone()
            if run is None:
                return {
                    "as_of": None,
                    "source": _source_label(is_shadow),
                    "provider": None,
                    "freshness": _freshness(None, 1, 2),
                    "degraded": True,
                    "sample_count": 0,
                    "ideas": [],
                }
            rows = connection.execute(
                """
                SELECT symbol, score, action, suggested_amount, last_price,
                       metrics_json, reasons_json, risks_json
                FROM scores WHERE run_id = ?
                ORDER BY score DESC, symbol LIMIT ?
                """,
                (run["id"], max(1, min(limit, 100))),
            ).fetchall()
            previous_run = connection.execute(
                """
                SELECT r.id
                FROM runs r JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = ? AND r.id < ?
                  AND r.market_degraded = 0
                  AND EXISTS (SELECT 1 FROM scores s WHERE s.run_id = r.id)
                ORDER BY r.id DESC LIMIT 1
                """,
                (int(is_shadow), run["id"]),
            ).fetchone()
            previous_rows = (
                connection.execute(
                    """
                    SELECT symbol, score, action, suggested_amount,
                           reasons_json, risks_json
                    FROM scores WHERE run_id = ?
                    ORDER BY score DESC, symbol
                    """,
                    (previous_run["id"],),
                ).fetchall()
                if previous_run
                else []
            )
            evidence_rows = connection.execute(
                """
                SELECT symbol, COUNT(*) AS contribution_count,
                       COUNT(DISTINCT provider) AS provider_count,
                       COUNT(DISTINCT category) AS category_count
                FROM score_contributions
                WHERE run_id = ?
                GROUP BY symbol
                """,
                (run["id"],),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT symbol, COUNT(*) AS event_count,
                       COUNT(DISTINCT provider) AS event_provider_count
                FROM normalized_events
                WHERE run_id = ?
                GROUP BY symbol
                """,
                (run["id"],),
            ).fetchall()
            outcome_rows = connection.execute(
                """
                SELECT s.symbol, o.horizon_days, o.return_pct,
                       o.relative_return_pct
                FROM score_outcomes o
                JOIN scores s ON s.run_id = o.run_id AND s.symbol = o.symbol
                WHERE s.symbol IN (
                    SELECT symbol FROM scores WHERE run_id = ?
                )
                """,
                (run["id"],),
            ).fetchall()
        outcomes: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in outcome_rows:
            outcomes[row["symbol"]].append(row)
        previous = {
            row["symbol"]: {
                "score": float(row["score"]),
                "rank": rank,
                "action": row["action"],
                "is_candidate": float(row["suggested_amount"]) > 0,
                "reasons": set(_json(row["reasons_json"], [])),
                "risks": set(_json(row["risks_json"], [])),
            }
            for rank, row in enumerate(previous_rows, start=1)
        }
        evidence = {row["symbol"]: dict(row) for row in evidence_rows}
        events = {row["symbol"]: dict(row) for row in event_rows}
        ideas = []
        for current_rank, row in enumerate(rows, start=1):
            if row["symbol"] in EXCLUDED_ANALYSIS_SYMBOLS:
                continue
            metrics = _json(row["metrics_json"], {})
            symbol_outcomes = outcomes.get(row["symbol"], [])
            reasons = _json(row["reasons_json"], [])
            risks = _json(row["risks_json"], [])
            movement = _signal_movement(
                row,
                current_rank,
                previous.get(row["symbol"]),
                metrics,
                reasons,
                risks,
            )
            evidence_item = evidence.get(row["symbol"], {})
            event_item = events.get(row["symbol"], {})
            provider_count = max(
                int(evidence_item.get("provider_count") or 0),
                int(event_item.get("event_provider_count") or 0),
            )
            category_count = int(evidence_item.get("category_count") or 0)
            event_count = int(event_item.get("event_count") or 0)
            evidence_coverage = min(
                100,
                provider_count * 20
                + min(category_count, 4) * 10
                + min(event_count, 5) * 4
                + min(len(symbol_outcomes), 10) * 2,
            )
            ideas.append(
                {
                    "symbol": row["symbol"],
                    "score": row["score"],
                    "action": row["action"],
                    "suggested_amount": row["suggested_amount"],
                    "price": row["last_price"],
                    "setup": metrics.get("setup", metrics.get("market_setup", "unknown")),
                    "risk_level": metrics.get("risk_level", "unknown"),
                    "market_score": metrics.get("market_score"),
                    "catalyst_score": metrics.get("catalyst_score", 0),
                    "provider": metrics.get(
                        "catalyst_provider", run["catalyst_provider"]
                    ),
                    "reasons": reasons,
                    "risks": risks,
                    **movement,
                    "evidence_coverage": evidence_coverage,
                    "evidence_provider_count": provider_count,
                    "evidence_category_count": category_count,
                    "fresh_event_count": event_count,
                    "outcome_sample_count": len(symbol_outcomes),
                    "outcome_win_rate_pct": round(
                        sum(float(item["return_pct"]) > 0 for item in symbol_outcomes)
                        / len(symbol_outcomes)
                        * 100,
                        1,
                    )
                    if symbol_outcomes
                    else None,
                    "calibration": {
                        "horizon_days": metrics.get("calibration_horizon_days"),
                        "score_band": metrics.get("calibration_score_band"),
                        "sample_count": metrics.get("calibration_sample_count", 0),
                        "confidence": metrics.get(
                            "calibration_confidence", "unmeasured"
                        ),
                        "win_rate_pct": metrics.get("calibration_win_rate_pct"),
                        "median_return_pct": metrics.get(
                            "calibration_median_return_pct"
                        ),
                        "median_relative_return_pct": metrics.get(
                            "calibration_median_relative_return_pct"
                        ),
                    },
                }
            )
        healthy, warning = (
            (SHADOW_HEALTHY, SHADOW_WARNING)
            if is_shadow
            else (THREE_HOUR_HEALTHY, THREE_HOUR_WARNING)
        )
        return {
            "as_of": run["started_at"],
            "run_id": run["id"],
            "source": _source_label(is_shadow),
            "provider": run["catalyst_provider"],
            "freshness": _freshness(run["started_at"], healthy, warning),
            "degraded": bool(run["market_degraded"]),
            "sample_count": len(ideas),
            "ideas": ideas,
        }

    def stock(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            raise ValueError("Invalid symbol")
        if symbol in EXCLUDED_ANALYSIS_SYMBOLS:
            raise ValueError("Symbol is excluded by the portfolio privacy policy.")
        with self.connect() as connection:
            position = connection.execute(
                """
                SELECT p.symbol, p.quantity, p.average_cost, p.classification,
                       a.action, a.current_price, a.current_value, a.weight_pct,
                       a.return_from_cost_pct, a.daily_return_pct,
                       a.return_5d_pct, a.score, a.reasons_text, a.risks_text,
                       r.started_at, r.market_degraded, r.market_coverage_pct
                FROM portfolio_positions p
                JOIN portfolio_imports i ON i.id = p.import_id AND i.status = 'active'
                LEFT JOIN portfolio_monitor_runs r
                  ON r.id = (SELECT id FROM portfolio_monitor_runs ORDER BY id DESC LIMIT 1)
                LEFT JOIN portfolio_assessments a
                  ON a.run_id = r.id AND a.symbol = p.symbol
                WHERE p.symbol = ?
                """,
                (symbol,),
            ).fetchone()
            scores = connection.execute(
                """
                SELECT s.run_id, s.score, s.action, s.suggested_amount,
                       s.last_price, s.metrics_json, s.reasons_json,
                       s.risks_json, r.started_at, r.market_degraded,
                       c.catalyst_provider, c.is_shadow
                FROM scores s
                JOIN runs r ON r.id = s.run_id
                LEFT JOIN catalyst_runs c ON c.run_id = s.run_id
                WHERE s.symbol = ?
                ORDER BY s.run_id DESC LIMIT 24
                """,
                (symbol,),
            ).fetchall()
            events = connection.execute(
                """
                SELECT provider, category, headline, source, published_at,
                       url, relevance, sentiment
                FROM normalized_events
                WHERE symbol = ?
                ORDER BY COALESCE(published_at, '') DESC, id DESC LIMIT 30
                """,
                (symbol,),
            ).fetchall()
            outcomes = connection.execute(
                """
                SELECT horizon_days, COUNT(*) AS samples,
                       AVG(return_pct) AS average_return_pct,
                       AVG(relative_return_pct) AS average_relative_return_pct,
                       AVG(max_favorable_pct) AS average_favorable_pct,
                       AVG(max_adverse_pct) AS average_adverse_pct,
                       SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) AS wins
                FROM score_outcomes WHERE symbol = ?
                GROUP BY horizon_days ORDER BY horizon_days
                """,
                (symbol,),
            ).fetchall()
            action_history = connection.execute(
                """
                SELECT r.started_at, a.action, a.score, a.return_from_cost_pct,
                       r.market_degraded
                FROM portfolio_assessments a
                JOIN portfolio_monitor_runs r ON r.id = a.run_id
                WHERE a.symbol = ?
                ORDER BY r.id DESC LIMIT 30
                """,
                (symbol,),
            ).fetchall()
            contributions = connection.execute(
                """
                SELECT provider, category, score_delta, confidence, source,
                       summary, metadata_json
                FROM score_contributions
                WHERE symbol = ?
                ORDER BY id DESC LIMIT 30
                """,
                (symbol,),
            ).fetchall()
            fundamentals = connection.execute(
                """
                SELECT provider, as_of, metrics_json, source
                FROM fundamental_snapshots
                WHERE symbol = ?
                ORDER BY as_of DESC, id DESC LIMIT 5
                """,
                (symbol,),
            ).fetchall()
        score_items = []
        for row in scores:
            metrics = _json(row["metrics_json"], {})
            score_items.append(
                {
                    "run_id": row["run_id"],
                    "started_at": row["started_at"],
                    "score": row["score"],
                    "action": row["action"],
                    "suggested_amount": row["suggested_amount"],
                    "price": row["last_price"],
                    "metrics": metrics,
                    "reasons": _json(row["reasons_json"], []),
                    "risks": _json(row["risks_json"], []),
                    "provider": row["catalyst_provider"],
                    "source": _source_label(bool(row["is_shadow"])),
                    "degraded": bool(row["market_degraded"]),
                }
            )
        position_payload = None
        if position:
            position_payload = {
                key: position[key]
                for key in [
                    "symbol",
                    "quantity",
                    "average_cost",
                    "classification",
                    "action",
                    "current_price",
                    "current_value",
                    "weight_pct",
                    "return_from_cost_pct",
                    "daily_return_pct",
                    "return_5d_pct",
                    "score",
                    "started_at",
                    "market_degraded",
                    "market_coverage_pct",
                ]
            }
            position_payload["reasons"] = _split_text(position["reasons_text"])
            position_payload["risks"] = _split_text(position["risks_text"])
        latest_time = (
            position_payload["started_at"]
            if position_payload
            else score_items[0]["started_at"]
            if score_items
            else None
        )
        latest_degraded = (
            bool(position_payload["market_degraded"])
            if position_payload
            else bool(score_items[0]["degraded"])
            if score_items
            else True
        )
        event_payload = [dict(row) for row in events]
        contribution_payload = [
            {**dict(row), "metadata": _json(row["metadata_json"], {})}
            for row in contributions
        ]
        fundamental_payload = [
            {**dict(row), "metrics": _json(row["metrics_json"], {})}
            for row in fundamentals
        ]
        outcome_payload = [
            {
                **dict(row),
                "win_rate_pct": round(row["wins"] / row["samples"] * 100, 1),
            }
            for row in outcomes
        ]
        return {
            "as_of": _now().isoformat(),
            "source": "stock_detail",
            "provider": sorted(
                {item["provider"] for item in score_items if item["provider"]}
            ),
            "freshness": _freshness(
                latest_time, THREE_HOUR_HEALTHY, THREE_HOUR_WARNING
            ),
            "degraded": latest_degraded,
            "symbol": symbol,
            "position": position_payload,
            "scores": score_items,
            "events": event_payload,
            "contributions": contribution_payload,
            "fundamentals": fundamental_payload,
            "dossier": _sourced_dossier(
                symbol,
                fundamental_payload,
                event_payload,
                contribution_payload,
                outcome_payload,
            ),
            "outcomes": outcome_payload,
            "action_history": [dict(row) for row in action_history],
            "sample_count": len(scores),
        }

    def performance(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.symbol, o.horizon_days, o.return_pct, o.relative_return_pct,
                       o.max_favorable_pct, o.max_adverse_pct,
                       s.score, s.action, s.metrics_json,
                       COALESCE(c.is_shadow, 0) AS is_shadow
                FROM score_outcomes o
                JOIN scores s ON s.run_id = o.run_id AND s.symbol = o.symbol
                LEFT JOIN catalyst_runs c ON c.run_id = o.run_id
                ORDER BY o.horizon_days
                """
            ).fetchall()
        rows = [
            row for row in rows if row["symbol"] not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        groups: dict[tuple[Any, ...], list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            metrics = _json(row["metrics_json"], {})
            setup = str(metrics.get("setup", metrics.get("market_setup", "unknown")))
            score = float(row["score"])
            score_band = (
                "90-100"
                if score >= 90
                else "80-89"
                if score >= 80
                else "70-79"
                if score >= 70
                else "<70"
            )
            dimensions = {
                "overall": "All signals",
                "score_band": score_band,
                "action": row["action"],
                "setup": setup,
                "source": _source_label(bool(row["is_shadow"])),
            }
            for dimension, label in dimensions.items():
                groups[(dimension, label, row["horizon_days"])].append(row)
        summaries = []
        for (dimension, label, horizon), items in groups.items():
            returns = [float(item["return_pct"]) for item in items]
            relatives = [
                float(item["relative_return_pct"])
                for item in items
                if item["relative_return_pct"] is not None
            ]
            summaries.append(
                {
                    "dimension": dimension,
                    "label": label,
                    "horizon_days": horizon,
                    "sample_count": len(items),
                    "win_rate_pct": round(
                        sum(value > 0 for value in returns) / len(returns) * 100, 1
                    ),
                    "average_return_pct": round(sum(returns) / len(returns), 2),
                    "median_return_pct": _median(returns),
                    "average_relative_return_pct": (
                        round(sum(relatives) / len(relatives), 2)
                        if relatives
                        else None
                    ),
                    "average_favorable_pct": round(
                        sum(float(item["max_favorable_pct"]) for item in items)
                        / len(items),
                        2,
                    ),
                    "average_adverse_pct": round(
                        sum(float(item["max_adverse_pct"]) for item in items)
                        / len(items),
                        2,
                    ),
                }
            )
        return {
            "as_of": _now().isoformat(),
            "source": "measured_outcomes",
            "provider": "stored_forward_outcomes",
            "freshness": {
                "status": "healthy",
                "age_hours": 0.0,
                "label": "Computed from stored outcomes",
            },
            "degraded": False,
            "sample_count": len(rows),
            "summaries": summaries,
        }

    def health(self) -> dict[str, Any]:
        with self.connect() as connection:
            production = connection.execute(
                """
                SELECT r.started_at, r.market_coverage_pct, r.market_degraded,
                       r.market_failures_json, r.analysis_status,
                       r.notification_status, r.notification_format,
                       r.notification_message
                FROM runs r JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = 0 ORDER BY r.id DESC LIMIT 1
                """
            ).fetchone()
            shadow = connection.execute(
                """
                SELECT r.started_at, r.market_coverage_pct, r.market_degraded,
                       r.market_failures_json, r.analysis_status,
                       r.notification_status, r.notification_format,
                       r.notification_message
                FROM runs r JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = 1 ORDER BY r.id DESC LIMIT 1
                """
            ).fetchone()
            portfolio = connection.execute(
                """
                SELECT started_at, market_coverage_pct, market_degraded,
                       analysis_status, notification_status,
                       notification_format, notification_message
                FROM portfolio_monitor_runs ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            price_watch = connection.execute(
                """
                SELECT captured_at, COUNT(*) AS snapshots,
                       SUM(CASE WHEN degraded = 0 THEN 1 ELSE 0 END) AS usable
                FROM portfolio_price_snapshots
                GROUP BY captured_at
                ORDER BY captured_at DESC LIMIT 1
                """
            ).fetchone()
            eod = connection.execute(
                """
                SELECT run_at, market_coverage_pct, degraded,
                       notification_status, notification_format,
                       notification_message
                FROM portfolio_eod_reports
                ORDER BY run_at DESC LIMIT 1
                """
            ).fetchone()
            providers = connection.execute(
                """
                SELECT provider, COUNT(*) AS calls,
                       SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_hits,
                       SUM(CASE WHEN status = 'plan_limited' THEN 1 ELSE 0 END)
                           AS plan_limited,
                       MAX(called_at) AS last_called_at
                FROM provider_calls
                WHERE called_at >= ?
                GROUP BY provider ORDER BY calls DESC
                """,
                ((_now() - timedelta(days=7)).isoformat(),),
            ).fetchall()
        shadow_promotion = self._shadow_promotion_snapshot(days=7)
        services = [
            _run_health(
                "Production scanner",
                production,
                THREE_HOUR_HEALTHY,
                THREE_HOUR_WARNING,
            ),
            _run_health(
                "Shadow scanner",
                shadow,
                SHADOW_HEALTHY,
                SHADOW_WARNING,
            ),
            _portfolio_health(portfolio),
            _price_watch_health(price_watch),
        ]
        if production:
            services.append(
                _notification_health("Universe PDF delivery", production)
            )
        if portfolio:
            services.append(
                _notification_health("Portfolio PDF delivery", portfolio)
            )
        if eod:
            services.append(_eod_health(eod))
            services.append(_notification_health("EOD PDF delivery", eod))
        launch_agents = [
            _launch_agent_status("com.stock-analyzer.local"),
            _launch_agent_status("com.stock-analyzer.shadow"),
            _launch_agent_status("com.stock-analyzer.portfolio"),
            _launch_agent_status("com.stock-analyzer.portfolio-price-watch"),
            _launch_agent_status("com.stock-analyzer.portfolio-eod"),
            _launch_agent_status("com.stock-analyzer.dashboard"),
        ]
        services.append(_scheduler_health(launch_agents))
        return {
            "as_of": _now().isoformat(),
            "source": "operations",
            "provider": "sqlite_and_launchctl",
            "freshness": {
                "status": "healthy",
                "age_hours": 0.0,
                "label": "Live status",
            },
            "degraded": any(item["status"] == "critical" for item in services),
            "sample_count": len(providers),
            "services": services,
            "shadow_promotion": shadow_promotion,
            "providers": [
                {
                    "provider": row["provider"],
                    "calls": row["calls"],
                    "success_rate_pct": round(
                        row["successes"] / row["calls"] * 100, 1
                    ),
                    "cache_hits": row["cache_hits"],
                    "cache_rate_pct": round(
                        row["cache_hits"] / row["calls"] * 100, 1
                    ),
                    "plan_limited": row["plan_limited"],
                    "role": _provider_role(row["provider"]),
                    "activation_state": _provider_activation_state(
                        row["provider"],
                        int(row["plan_limited"] or 0),
                        round(row["successes"] / row["calls"] * 100, 1),
                    ),
                    "last_called_at": row["last_called_at"],
                }
                for row in providers
            ],
            "launch_agents": launch_agents,
        }

    def _shadow_promotion_snapshot(self, days: int) -> dict[str, Any]:
        cutoff = _now() - timedelta(days=days)
        with self.connect() as connection:
            runs = connection.execute(
                """
                SELECT r.id, r.started_at
                FROM runs r
                JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = 1
                  AND r.started_at >= ?
                  AND r.market_degraded = 0
                  AND EXISTS (SELECT 1 FROM scores s WHERE s.run_id = r.id)
                ORDER BY r.started_at
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            calls = connection.execute(
                """
                SELECT pc.provider, pc.ok, pc.cache_hit, pc.status
                FROM provider_calls pc
                JOIN catalyst_runs c ON c.run_id = pc.run_id
                JOIN runs r ON r.id = pc.run_id
                WHERE c.is_shadow = 1
                  AND r.started_at >= ?
                  AND r.market_degraded = 0
                  AND pc.provider != 'sec'
                  AND EXISTS (SELECT 1 FROM scores s WHERE s.run_id = r.id)
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            positive_rows = connection.execute(
                """
                SELECT sc.score_delta
                FROM score_contributions sc
                JOIN catalyst_runs c ON c.run_id = sc.run_id
                JOIN runs r ON r.id = sc.run_id
                WHERE c.is_shadow = 1
                  AND r.started_at >= ?
                  AND r.market_degraded = 0
                  AND sc.score_delta > 0
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            duplicates = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM (
                    SELECT sc.run_id, sc.symbol, sc.event_id
                    FROM score_contributions sc
                    JOIN catalyst_runs c ON c.run_id = sc.run_id
                    JOIN runs r ON r.id = sc.run_id
                    WHERE c.is_shadow = 1
                      AND r.started_at >= ?
                      AND r.market_degraded = 0
                      AND sc.category = 'news'
                      AND sc.event_id != ''
                    GROUP BY sc.run_id, sc.symbol, sc.event_id
                    HAVING COUNT(*) > 1
                )
                """,
                (cutoff.isoformat(),),
            ).fetchone()
            score_rows = connection.execute(
                """
                SELECT s.run_id, s.symbol, s.action, r.started_at
                FROM scores s
                JOIN runs r ON r.id = s.run_id
                JOIN catalyst_runs c ON c.run_id = s.run_id
                WHERE c.is_shadow = 1
                  AND r.started_at >= ?
                  AND r.market_degraded = 0
                ORDER BY s.symbol, r.started_at
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            reviewed = {
                (row["run_id"], row["symbol"])
                for row in connection.execute(
                    """
                    SELECT sr.run_id, sr.symbol
                    FROM shadow_reviews sr
                    JOIN runs r ON r.id = sr.run_id
                    WHERE r.started_at >= ?
                    """,
                    (cutoff.isoformat(),),
                ).fetchall()
            }

        remote_calls = [row for row in calls if not row["cache_hit"]]
        success_rate = (
            sum(int(row["ok"]) for row in remote_calls) / len(remote_calls) * 100
            if remote_calls
            else 100.0
        )
        positives = sorted(float(row["score_delta"]) for row in positive_rows)
        p95 = 0.0
        if positives:
            index = max(
                0,
                min(len(positives) - 1, int(0.95 * len(positives) + 0.999) - 1),
            )
            p95 = positives[index]
        candidate_changes: list[tuple[int, str]] = []
        previous_by_symbol: dict[str, str] = {}
        for row in score_rows:
            previous = previous_by_symbol.get(row["symbol"])
            if row["action"] == "candidate" and previous not in {None, "candidate"}:
                candidate_changes.append((row["run_id"], row["symbol"]))
            previous_by_symbol[row["symbol"]] = row["action"]
        unreviewed = [change for change in candidate_changes if change not in reviewed]
        started_at = [_parse_datetime(row["started_at"]) for row in runs]
        parsed_started_at = [item for item in started_at if item is not None]
        span_days = (
            (max(parsed_started_at) - min(parsed_started_at)).total_seconds() / 86400
            if len(parsed_started_at) >= 2
            else 0.0
        )
        status = {
            "window_days": days,
            "scan_count": len(runs),
            "span_days": round(span_days, 2),
            "remote_call_count": len(remote_calls),
            "provider_success_rate_pct": round(success_rate, 2),
            "provider_summaries": shadow_provider_summaries(remote_calls),
            "positive_contribution_p95": round(p95, 2),
            "duplicate_news_contributions": (
                int(duplicates["count"]) if duplicates else 0
            ),
            "candidate_state_changes": len(candidate_changes),
            "unreviewed_candidate_changes": unreviewed,
        }
        return shadow_promotion_gate(status)


def _signal_movement(
    row: sqlite3.Row,
    current_rank: int,
    previous: dict[str, Any] | None,
    metrics: dict[str, Any],
    reasons: list[str],
    risks: list[str],
) -> dict[str, Any]:
    stored_state = metrics.get("signal_state")
    if previous is None:
        return {
            "signal_state": stored_state
            or ("new_candidate" if float(row["suggested_amount"]) > 0 else "new_coverage"),
            "current_rank": current_rank,
            "previous_rank": None,
            "rank_delta": None,
            "previous_score": None,
            "score_delta": None,
            "new_reasons": metrics.get("new_reasons", reasons[:3]),
            "new_risks": metrics.get("new_risks", risks[:2]),
            "resolved_risks": metrics.get("resolved_risks", []),
        }
    score_delta = round(float(row["score"]) - float(previous["score"]), 1)
    rank_delta = int(previous["rank"]) - current_rank
    is_candidate = float(row["suggested_amount"]) > 0
    if is_candidate and not previous["is_candidate"]:
        state = "new_candidate"
    elif not is_candidate and previous["is_candidate"]:
        state = "lost_candidate"
    elif score_delta >= 5 or rank_delta >= 10:
        state = "upgraded"
    elif score_delta <= -5 or rank_delta <= -10:
        state = "downgraded"
    else:
        state = "steady"
    return {
        "signal_state": stored_state or state,
        "current_rank": current_rank,
        "previous_rank": metrics.get("previous_rank", previous["rank"]),
        "rank_delta": metrics.get("rank_delta", rank_delta),
        "previous_score": metrics.get("previous_score", previous["score"]),
        "score_delta": metrics.get("score_delta", score_delta),
        "new_reasons": metrics.get(
            "new_reasons",
            [item for item in reasons if item not in previous["reasons"]][:3],
        ),
        "new_risks": metrics.get(
            "new_risks",
            [item for item in risks if item not in previous["risks"]][:2],
        ),
        "resolved_risks": metrics.get(
            "resolved_risks",
            [item for item in previous["risks"] if item not in set(risks)][:2],
        ),
    }


def _allocation(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    large = [item for item in positions if item["allocation_pct"] >= 2]
    small = [item for item in positions if item["allocation_pct"] < 2]
    result = [
        {
            "symbol": item["symbol"],
            "allocation_pct": round(item["allocation_pct"], 2),
            "value": round(item["value"], 2),
        }
        for item in large
    ]
    if small:
        result.append(
            {
                "symbol": "Other",
                "allocation_pct": round(
                    sum(item["allocation_pct"] for item in small), 2
                ),
                "value": round(sum(item["value"] for item in small), 2),
            }
        )
    return result


def _portfolio_streaks(rows: list[sqlite3.Row]) -> dict[str, dict[str, int]]:
    by_symbol: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["market_degraded"] or float(row["market_coverage_pct"]) < 90:
            continue
        by_symbol[row["symbol"]].append(row["action"])
    result = {}
    for symbol, newest_first in by_symbol.items():
        streak = 0
        current = newest_first[0]
        for action in newest_first:
            if action != current:
                break
            streak += 1
        chronological = list(reversed(newest_first))
        transitions = sum(
            previous != current_action
            for previous, current_action in zip(
                chronological, chronological[1:]
            )
        )
        result[symbol] = {"streak": streak, "transitions": transitions}
    return result


def _run_health(
    name: str,
    row: sqlite3.Row | None,
    healthy_hours: float,
    warning_hours: float,
) -> dict[str, Any]:
    if row is None:
        return {"name": name, "status": "critical", "detail": "No recorded run", "as_of": None}
    freshness = _freshness(row["started_at"], healthy_hours, warning_hours)
    status = freshness["status"]
    if row["market_degraded"] or row["analysis_status"] != "completed":
        status = "critical"
    failures = _json(row["market_failures_json"], [])
    detail = (
        f"{freshness['label']} · {float(row['market_coverage_pct']):.1f}% coverage"
        + (" · degraded" if row["market_degraded"] else "")
        + (f" · {len(failures)} failed symbols" if failures else "")
    )
    return {"name": name, "status": status, "detail": detail, "as_of": row["started_at"]}


def _portfolio_health(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "name": "Portfolio monitor",
            "status": "critical",
            "detail": "No recorded run",
            "as_of": None,
        }
    freshness = _freshness(
        row["started_at"], THREE_HOUR_HEALTHY, THREE_HOUR_WARNING
    )
    status = freshness["status"]
    if row["market_degraded"] or row["analysis_status"] != "completed":
        status = "critical"
    return {
        "name": "Portfolio monitor",
        "status": status,
        "detail": (
            f"{freshness['label']} · {float(row['market_coverage_pct']):.1f}% coverage"
            + (" · degraded" if row["market_degraded"] else "")
        ),
        "as_of": row["started_at"],
    }


def _price_watch_health(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None or row["captured_at"] is None:
        return {
            "name": "Portfolio price watch",
            "status": "warning",
            "detail": "No intraday price snapshots yet",
            "as_of": None,
        }
    freshness = _freshness(row["captured_at"], 0.75, 1.5)
    snapshots = int(row["snapshots"] or 0)
    usable = int(row["usable"] or 0)
    status = freshness["status"]
    if snapshots and usable / snapshots < 0.9:
        status = "critical"
    elif status != "healthy" and not _is_regular_market_hours():
        status = "healthy"
        freshness = {
            **freshness,
            "label": f"idle outside market hours · last {freshness['label']}",
        }
    return {
        "name": "Portfolio price watch",
        "status": status,
        "detail": f"{freshness['label']} · {usable}/{snapshots} prices usable",
        "as_of": row["captured_at"],
    }


def _eod_health(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "name": "Portfolio EOD report",
            "status": "warning",
            "detail": "No EOD report yet",
            "as_of": None,
        }
    freshness = _freshness(row["run_at"], 32.0, 48.0)
    status = "critical" if row["degraded"] else freshness["status"]
    return {
        "name": "Portfolio EOD report",
        "status": status,
        "detail": (
            f"{freshness['label']} · {float(row['market_coverage_pct']):.1f}% coverage"
            + (" · degraded" if row["degraded"] else "")
        ),
        "as_of": row["run_at"],
    }


def _notification_health(name: str, row: sqlite3.Row) -> dict[str, Any]:
    notification = row["notification_status"]
    if notification in {"delivered", "dry_run", "not_applicable"}:
        status = "healthy"
    elif notification == "unknown":
        status = "warning"
    else:
        status = "critical"
    notification_format = row["notification_format"] or "legacy"
    detail = f"{notification} via {notification_format}"
    if row["notification_message"]:
        detail += f" - {row['notification_message']}"
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "as_of": row["started_at"] if "started_at" in row.keys() else row["run_at"],
    }


def _sourced_dossier(
    symbol: str,
    fundamentals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_fundamentals = fundamentals[0] if fundamentals else None
    fundamental_metrics = latest_fundamentals["metrics"] if latest_fundamentals else {}
    fundamental_items = [
        {
            "label": label,
            "value": fundamental_metrics[key],
            "source": latest_fundamentals["source"],
            "provider": latest_fundamentals["provider"],
            "as_of": latest_fundamentals["as_of"],
        }
        for key, label in [
            ("revenue_growth_yoy_pct", "Revenue growth YoY %"),
            ("net_margin_pct", "Net margin %"),
            ("cash", "Cash"),
            ("debt", "Debt"),
            ("net_cash", "Net cash"),
            ("free_cash_flow", "Free cash flow"),
            ("free_cash_flow_growth_yoy_pct", "Free cash flow growth YoY %"),
            ("shares_growth_yoy_pct", "Shares growth YoY %"),
        ]
        if key in fundamental_metrics
    ]
    available_labels = {item["label"] for item in fundamental_items}
    expected_fundamentals = [
        "Revenue growth YoY %",
        "Net margin %",
        "Cash",
        "Debt",
        "Net cash",
        "Free cash flow",
        "Free cash flow growth YoY %",
        "Shares growth YoY %",
        "Market cap",
        "Valuation ratio",
    ]
    filing_events = [
        event
        for event in events
        if str(event.get("category", "")).lower() in {"filing", "insider"}
        or str(event.get("source", "")).lower() == "sec"
    ][:8]
    contribution_items = [
        item
        for item in contributions
        if item.get("category") in {
            "filings_insiders",
            "fundamentals_analyst",
            "earnings",
            "news",
        }
    ][:10]
    return {
        "symbol": symbol,
        "methodology": (
            "Evidence-only dossier from stored provider events, score "
            "contributions, fundamental snapshots, and measured outcomes. "
            "No model-generated price targets or unsourced estimates."
        ),
        "fundamentals": {
            "status": "available" if fundamental_items else "unavailable",
            "items": fundamental_items,
            "unavailable": [
                label for label in expected_fundamentals if label not in available_labels
            ],
        },
        "filing_catalysts": {
            "status": "available" if filing_events else "unavailable",
            "items": filing_events,
        },
        "score_evidence": {
            "status": "available" if contribution_items else "unavailable",
            "items": contribution_items,
        },
        "measured_outcomes": {
            "status": "available" if outcomes else "unavailable",
            "items": outcomes,
        },
    }


def _provider_role(provider: str) -> str:
    if provider == "sec":
        return "production evidence"
    if provider == "fmp":
        return "dormant smoke-test"
    if provider in {"finnhub", "marketaux", "alpha_vantage", "fred"}:
        return "shadow context"
    return "context"


def _provider_activation_state(
    provider: str,
    plan_limited: int,
    success_rate_pct: float,
) -> str:
    if provider == "sec":
        return "active production"
    if plan_limited:
        return "blocked by plan limits"
    if provider in {"finnhub", "marketaux", "alpha_vantage", "fred"}:
        return (
            "shadow gate passing access"
            if success_rate_pct >= 95
            else "shadow gate needs reliability"
        )
    return "observed"


def _scheduler_health(agents: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [
        item
        for item in agents
        if item["last_exit_code"] not in {None, "0"}
    ]
    unavailable = [
        item["label"].replace("com.stock-analyzer.", "")
        for item in agents
        if item["state"] in {"not_loaded", "unavailable"}
    ]
    if failed:
        return {
            "name": "Schedulers",
            "status": "critical",
            "detail": ", ".join(
                f"{item['label'].replace('com.stock-analyzer.', '')} exit {item['last_exit_code']}"
                for item in failed
            ),
            "as_of": None,
        }
    if unavailable:
        return {
            "name": "Schedulers",
            "status": "warning",
            "detail": "Not loaded: " + ", ".join(unavailable),
            "as_of": None,
        }
    return {
        "name": "Schedulers",
        "status": "healthy",
        "detail": "Loaded; idle interval jobs are normal",
        "as_of": None,
    }


def _launch_agent_status(label: str) -> dict[str, Any]:
    if not hasattr(os, "getuid"):
        return {"label": label, "state": "unavailable", "last_exit_code": None}
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"label": label, "state": "unavailable", "last_exit_code": None}
    if result.returncode:
        return {"label": label, "state": "not_loaded", "last_exit_code": None}
    state_match = re.search(r"^\s*state = (.+)$", result.stdout, re.MULTILINE)
    exit_match = re.search(
        r"^\s*last exit code = (?:\d+:\s*)?([A-Z_]+|\d+)",
        result.stdout,
        re.MULTILINE,
    )
    return {
        "label": label,
        "state": state_match.group(1).strip() if state_match else "loaded",
        "last_exit_code": exit_match.group(1) if exit_match else None,
    }


def create_dashboard_app(db_path: Path) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    store = DashboardStore(db_path)

    @app.before_request
    def restrict_loopback() -> None:
        host = request.host.split(":", 1)[0].lower()
        if request.host.startswith("[::1]"):
            host = "[::1]"
        if host not in ALLOWED_HOSTS:
            abort(403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            abort(405)

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/api/overview")
    def overview():
        return jsonify(store.overview())

    @app.get("/api/portfolio")
    def portfolio():
        return jsonify(store.portfolio())

    @app.get("/api/ideas")
    def ideas():
        source = request.args.get("source", "production")
        try:
            return jsonify(store.ideas(source))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/stocks/<symbol>")
    def stock(symbol: str):
        try:
            return jsonify(store.stock(symbol))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/performance")
    def performance():
        return jsonify(store.performance())

    @app.get("/api/health")
    def health():
        return jsonify(store.health())

    return app


def run_dashboard(db_path: Path, port: int = 8765) -> None:
    if not db_path.exists():
        raise ValueError(f"Dashboard database does not exist: {db_path}")
    if port < 1 or port > 65535:
        raise ValueError("Dashboard port must be between 1 and 65535.")
    app = create_dashboard_app(db_path)
    server = make_server("127.0.0.1", port, app, threaded=True)
    print(f"Decision cockpit available at http://127.0.0.1:{port}")
    server.serve_forever()
