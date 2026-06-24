from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS
from stock_analyzer.models import StockScore
from stock_analyzer.outcomes import summarize_episode_calibration
from stock_analyzer.portfolio_models import (
    PortfolioAssessment,
    PortfolioDiff,
    PortfolioEodReport,
    PortfolioPolicy,
    PortfolioPosition,
    PortfolioPriceAlert,
    PortfolioPriceSnapshot,
)

PORTFOLIO_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
PORTFOLIO_IDENTIFIER_PATTERN = re.compile(r"^[A-Z]\d{8,}$")
PORTFOLIO_PARSER_VERSION_PATTERN = re.compile(r"^[a-z0-9.-]{1,64}$")
PORTFOLIO_BLOCKED_SYMBOL_PREFIXES = ("ACCOUNT", "ACCT", "GRANT", "RSU")
PORTFOLIO_BLOCKED_SYMBOLS = {"FCASH", *EXCLUDED_ANALYSIS_SYMBOLS}
PORTFOLIO_CLASSIFICATIONS = {
    "adaptive",
    "core_etf",
    "thematic_etf",
    "established",
    "growth_cyclical",
    "speculative",
}
SHADOW_GATE_MIN_SCANS = 20
SHADOW_GATE_MIN_SPAN_DAYS = 7.0
SHADOW_GATE_MIN_PROVIDER_SUCCESS_PCT = 95.0
SHADOW_GATE_MAX_POSITIVE_P95 = 8.0


def _validate_portfolio_preview_input(
    statement_date: str,
    parser_version: str,
    positions: list[PortfolioPosition],
) -> None:
    valid = bool(positions) and bool(
        PORTFOLIO_PARSER_VERSION_PATTERN.fullmatch(parser_version)
    )
    try:
        valid = valid and date.fromisoformat(statement_date).isoformat() == statement_date
    except (TypeError, ValueError):
        valid = False
    symbols: set[str] = set()
    for position in positions:
        valid = valid and bool(PORTFOLIO_SYMBOL_PATTERN.fullmatch(position.symbol))
        valid = valid and not bool(
            PORTFOLIO_IDENTIFIER_PATTERN.fullmatch(position.symbol)
        )
        valid = valid and position.symbol not in PORTFOLIO_BLOCKED_SYMBOLS
        valid = valid and not position.symbol.startswith(
            PORTFOLIO_BLOCKED_SYMBOL_PREFIXES
        )
        valid = valid and position.symbol not in symbols
        valid = valid and position.classification in PORTFOLIO_CLASSIFICATIONS
        valid = valid and math.isfinite(position.quantity) and position.quantity > 0
        valid = valid and math.isfinite(position.average_cost) and position.average_cost > 0
        symbols.add(position.symbol)
    if not valid:
        raise ValueError("Portfolio preview rejected by data-minimization policy.")


def _exclude_portfolio_symbols(
    positions: list[PortfolioPosition],
) -> list[PortfolioPosition]:
    return [
        position
        for position in positions
        if position.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
    ]


def _sanitize_excluded_payload(payload: object) -> object:
    if isinstance(payload, dict):
        identity_values = {
            str(payload.get(key, "")).strip().upper()
            for key in ("symbol", "ticker")
        }
        if identity_values & EXCLUDED_ANALYSIS_SYMBOLS:
            return None
        return {
            key: sanitized
            for key, value in payload.items()
            if str(key).strip().upper() not in EXCLUDED_ANALYSIS_SYMBOLS
            if (sanitized := _sanitize_excluded_payload(value)) is not None
        }
    if isinstance(payload, list):
        return [
            sanitized
            for value in payload
            if (sanitized := _sanitize_excluded_payload(value)) is not None
        ]
    if isinstance(payload, str) and payload.strip().upper() in EXCLUDED_ANALYSIS_SYMBOLS:
        return None
    return payload


def _median(values: list[float]) -> float:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _portfolio_diff(
    previous: list[PortfolioPosition],
    current: list[PortfolioPosition],
) -> PortfolioDiff:
    old = {position.symbol: position for position in previous}
    new = {position.symbol: position for position in current}
    added = [new[symbol] for symbol in sorted(new.keys() - old.keys())]
    removed = [old[symbol] for symbol in sorted(old.keys() - new.keys())]
    changed = [
        (old[symbol], new[symbol])
        for symbol in sorted(old.keys() & new.keys())
        if (
            abs(old[symbol].quantity - new[symbol].quantity) > 1e-6
            or abs(old[symbol].average_cost - new[symbol].average_cost) > 0.005
        )
    ]
    return PortfolioDiff(added=added, removed=removed, changed=changed)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    universe_source TEXT NOT NULL,
    universe_size INTEGER NOT NULL,
    market_requested INTEGER DEFAULT 0,
    market_received INTEGER DEFAULT 0,
    market_coverage_pct REAL DEFAULT 0,
    market_degraded INTEGER DEFAULT 0,
    market_failures_json TEXT DEFAULT '[]',
    top_symbol TEXT,
    top_score REAL,
    alert_count INTEGER DEFAULT 0,
    analysis_status TEXT NOT NULL DEFAULT 'completed',
    notification_status TEXT NOT NULL DEFAULT 'pending',
    notification_format TEXT NOT NULL DEFAULT '',
    notification_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    score REAL NOT NULL,
    action TEXT NOT NULL,
    suggested_amount REAL NOT NULL,
    last_price REAL NOT NULL,
    metrics_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_scores_run_id ON scores(run_id);
CREATE INDEX IF NOT EXISTS idx_scores_symbol ON scores(symbol);

CREATE TABLE IF NOT EXISTS provider_cache (
    provider TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(provider, cache_key)
);

CREATE TABLE IF NOT EXISTS provider_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    portfolio_run_id INTEGER,
    called_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    symbol TEXT,
    ok INTEGER NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER DEFAULT 0,
    cache_hit INTEGER DEFAULT 0,
    message TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id),
    FOREIGN KEY(portfolio_run_id) REFERENCES portfolio_monitor_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_provider_calls_provider_time
ON provider_calls(provider, called_at);

CREATE TABLE IF NOT EXISTS normalized_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    category TEXT NOT NULL,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    url TEXT NOT NULL,
    relevance REAL NOT NULL,
    sentiment REAL,
    UNIQUE(run_id, symbol, provider, event_id),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS score_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    category TEXT NOT NULL,
    score_delta REAL NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    summary TEXT NOT NULL,
    event_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_contributions_run_symbol
ON score_contributions(run_id, symbol);

CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    as_of TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(run_id, symbol, provider),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol_time
ON fundamental_snapshots(symbol, as_of);

CREATE TABLE IF NOT EXISTS catalyst_runs (
    run_id INTEGER PRIMARY KEY,
    catalyst_provider TEXT NOT NULL,
    is_shadow INTEGER NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS shadow_reviews (
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT NOT NULL,
    PRIMARY KEY(run_id, symbol),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS score_outcomes (
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    return_pct REAL NOT NULL,
    benchmark_return_pct REAL,
    relative_return_pct REAL,
    max_favorable_pct REAL NOT NULL,
    max_adverse_pct REAL NOT NULL,
    PRIMARY KEY(run_id, symbol, horizon_days),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_score_outcomes_horizon
ON score_outcomes(horizon_days);

CREATE TABLE IF NOT EXISTS portfolio_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    statement_date TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    base_import_id INTEGER,
    applied_at TEXT,
    FOREIGN KEY(base_import_id) REFERENCES portfolio_imports(id)
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    import_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    average_cost REAL NOT NULL,
    classification TEXT NOT NULL,
    PRIMARY KEY(import_id, symbol),
    FOREIGN KEY(import_id) REFERENCES portfolio_imports(id)
);

CREATE TABLE IF NOT EXISTS portfolio_policies (
    symbol TEXT PRIMARY KEY,
    classification_override TEXT,
    concentration_exempt INTEGER NOT NULL DEFAULT 0,
    buy_more_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_monitor_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    import_id INTEGER NOT NULL,
    market_coverage_pct REAL NOT NULL,
    market_degraded INTEGER NOT NULL,
    total_invested_value REAL NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'completed',
    notification_status TEXT NOT NULL DEFAULT 'pending',
    notification_format TEXT NOT NULL DEFAULT '',
    notification_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    FOREIGN KEY(import_id) REFERENCES portfolio_imports(id)
);

CREATE TABLE IF NOT EXISTS portfolio_assessments (
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    classification TEXT NOT NULL,
    current_price REAL NOT NULL,
    current_value REAL NOT NULL,
    weight_pct REAL NOT NULL,
    return_from_cost_pct REAL NOT NULL,
    daily_return_pct REAL,
    return_5d_pct REAL,
    score REAL NOT NULL,
    reasons_text TEXT NOT NULL,
    risks_text TEXT NOT NULL,
    PRIMARY KEY(run_id, symbol),
    FOREIGN KEY(run_id) REFERENCES portfolio_monitor_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_assessments_action
ON portfolio_assessments(action);

CREATE TABLE IF NOT EXISTS portfolio_price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    previous_close REAL NOT NULL,
    baseline_price REAL NOT NULL,
    move_pct REAL NOT NULL,
    move_dollars REAL NOT NULL,
    position_value REAL NOT NULL,
    day_dollar_change REAL NOT NULL,
    source TEXT NOT NULL,
    freshness_seconds INTEGER,
    degraded INTEGER NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_price_snapshots_trade_symbol
ON portfolio_price_snapshots(trade_date, symbol);

CREATE TABLE IF NOT EXISTS portfolio_price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    threshold_pct REAL NOT NULL,
    triggered_at TEXT NOT NULL,
    baseline_price REAL NOT NULL,
    current_price REAL NOT NULL,
    move_pct REAL NOT NULL,
    move_dollars REAL NOT NULL,
    notification_status TEXT NOT NULL DEFAULT 'pending',
    notification_message TEXT NOT NULL DEFAULT '',
    UNIQUE(trade_date, symbol, direction, threshold_pct)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_price_alerts_trade_symbol
ON portfolio_price_alerts(trade_date, symbol);

CREATE TABLE IF NOT EXISTS portfolio_eod_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL UNIQUE,
    run_at TEXT NOT NULL,
    total_value REAL NOT NULL,
    start_value REAL NOT NULL,
    total_gain_dollars REAL NOT NULL,
    total_loss_dollars REAL NOT NULL,
    net_change_dollars REAL NOT NULL,
    net_change_pct REAL NOT NULL,
    winner_count INTEGER NOT NULL,
    loser_count INTEGER NOT NULL,
    flat_count INTEGER NOT NULL,
    source TEXT NOT NULL,
    market_coverage_pct REAL NOT NULL,
    degraded INTEGER NOT NULL,
    notification_status TEXT NOT NULL DEFAULT 'pending',
    notification_format TEXT NOT NULL DEFAULT '',
    notification_message TEXT NOT NULL DEFAULT ''
);
"""


class StockDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._provider_call_capture: list[int] | None = None

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """
                UPDATE score_outcomes
                SET max_favorable_pct = MAX(max_favorable_pct, 0),
                    max_adverse_pct = MIN(max_adverse_pct, 0)
                WHERE max_favorable_pct < 0 OR max_adverse_pct > 0
                """
            )
            provider_call_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(provider_calls)").fetchall()
            }
            if "run_id" not in provider_call_columns:
                connection.execute("ALTER TABLE provider_calls ADD COLUMN run_id INTEGER")
            if "portfolio_run_id" not in provider_call_columns:
                connection.execute(
                    "ALTER TABLE provider_calls ADD COLUMN portfolio_run_id INTEGER"
                )
            run_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            run_migrations = {
                "market_requested": "INTEGER DEFAULT 0",
                "market_received": "INTEGER DEFAULT 0",
                "market_coverage_pct": "REAL DEFAULT 0",
                "market_degraded": "INTEGER DEFAULT 0",
                "market_failures_json": "TEXT DEFAULT '[]'",
                "analysis_status": "TEXT NOT NULL DEFAULT 'completed'",
                "notification_status": "TEXT NOT NULL DEFAULT 'pending'",
                "notification_format": "TEXT NOT NULL DEFAULT ''",
                "notification_message": "TEXT NOT NULL DEFAULT ''",
                "completed_at": "TEXT",
            }
            for column, definition in run_migrations.items():
                if column not in run_columns:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            portfolio_run_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(portfolio_monitor_runs)"
                ).fetchall()
            }
            portfolio_run_migrations = {
                "analysis_status": "TEXT NOT NULL DEFAULT 'completed'",
                "notification_status": "TEXT NOT NULL DEFAULT 'pending'",
                "notification_format": "TEXT NOT NULL DEFAULT ''",
                "notification_message": "TEXT NOT NULL DEFAULT ''",
                "completed_at": "TEXT",
            }
            for column, definition in portfolio_run_migrations.items():
                if column not in portfolio_run_columns:
                    connection.execute(
                        f"ALTER TABLE portfolio_monitor_runs ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                UPDATE runs
                SET completed_at = COALESCE(completed_at, started_at),
                    analysis_status = COALESCE(NULLIF(analysis_status, ''), 'completed'),
                    notification_status = CASE
                        WHEN notification_status IS NULL OR notification_status = ''
                        THEN 'unknown'
                        ELSE notification_status
                    END,
                    notification_format = COALESCE(notification_format, '')
                """
            )
            connection.execute(
                """
                UPDATE portfolio_monitor_runs
                SET completed_at = COALESCE(completed_at, started_at),
                    analysis_status = COALESCE(NULLIF(analysis_status, ''), 'completed'),
                    notification_status = CASE
                        WHEN notification_status IS NULL OR notification_status = ''
                        THEN 'unknown'
                        ELSE notification_status
                    END,
                    notification_format = COALESCE(notification_format, '')
                """
            )
            connection.execute(
                """
                UPDATE portfolio_monitor_runs
                SET notification_status = 'unknown'
                WHERE notification_status = 'pending'
                  AND completed_at = started_at
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provider_calls_run_id
                ON provider_calls(run_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provider_calls_portfolio_run_id
                ON provider_calls(portfolio_run_id)
                """
            )
            excluded = tuple(sorted(EXCLUDED_ANALYSIS_SYMBOLS))
            placeholders = ",".join("?" for _ in excluded)
            connection.execute(
                f"DELETE FROM score_outcomes WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM normalized_events WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM score_contributions WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM shadow_reviews WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM provider_calls WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM scores WHERE symbol IN ({placeholders})",
                excluded,
            )
            for symbol in excluded:
                connection.execute(
                    "DELETE FROM provider_cache WHERE UPPER(cache_key) LIKE ?",
                    (f"%{symbol}%",),
                )
                connection.execute(
                    """
                    DELETE FROM provider_cache
                    WHERE UPPER(payload_json) LIKE ?
                       OR UPPER(payload_json) LIKE ?
                    """,
                    (f"%{symbol}%", "%WALMART%"),
                )
            failure_rows = connection.execute(
                "SELECT id, market_failures_json FROM runs"
            ).fetchall()
            for row in failure_rows:
                try:
                    failures = json.loads(row["market_failures_json"] or "[]")
                except json.JSONDecodeError:
                    failures = []
                filtered = [
                    symbol
                    for symbol in failures
                    if str(symbol).upper() not in EXCLUDED_ANALYSIS_SYMBOLS
                ]
                if filtered != failures:
                    connection.execute(
                        "UPDATE runs SET market_failures_json = ? WHERE id = ?",
                        (json.dumps(filtered), row["id"]),
                    )
            connection.execute(
                f"DELETE FROM portfolio_assessments WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM portfolio_positions WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM portfolio_policies WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM portfolio_price_snapshots WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                f"DELETE FROM portfolio_price_alerts WHERE symbol IN ({placeholders})",
                excluded,
            )
            connection.execute(
                """
                UPDATE portfolio_monitor_runs
                SET total_invested_value = COALESCE(
                    (
                        SELECT SUM(current_value)
                        FROM portfolio_assessments
                        WHERE run_id = portfolio_monitor_runs.id
                    ),
                    0
                )
                """
            )
            connection.execute(
                """
                UPDATE runs
                SET top_symbol = (
                        SELECT symbol FROM scores
                        WHERE run_id = runs.id
                        ORDER BY score DESC, symbol
                        LIMIT 1
                    ),
                    top_score = (
                        SELECT score FROM scores
                        WHERE run_id = runs.id
                        ORDER BY score DESC, symbol
                        LIMIT 1
                    ),
                    alert_count = (
                        SELECT COUNT(*) FROM scores
                        WHERE run_id = runs.id AND suggested_amount > 0
                    )
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def create_run(
        self,
        started_at: datetime,
        provider: str,
        universe_source: str,
        universe_size: int,
        market_requested: int = 0,
        market_received: int = 0,
        market_coverage_pct: float = 0.0,
        market_degraded: bool = False,
        market_failures: list[str] | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    started_at, provider, universe_source, universe_size,
                    market_requested, market_received, market_coverage_pct,
                    market_degraded, market_failures_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at.isoformat(),
                    provider,
                    universe_source,
                    universe_size,
                    market_requested,
                    market_received,
                    market_coverage_pct,
                    int(market_degraded),
                    json.dumps(
                        [
                            symbol
                            for symbol in market_failures or []
                            if symbol.upper() not in EXCLUDED_ANALYSIS_SYMBOLS
                        ]
                    ),
                ),
            )
            return int(cursor.lastrowid)

    def insert_scores(self, run_id: int, scores: list[StockScore]) -> None:
        scores = [
            score
            for score in scores
            if score.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        rows = [
            (
                run_id,
                score.symbol,
                score.score,
                score.action,
                score.suggested_amount,
                score.last_price,
                json.dumps(
                    {
                        **score.metrics,
                        "setup": score.setup,
                        "risk_level": score.risk_level,
                        "market_score": score.market_score,
                        "catalyst_score": score.catalyst_score,
                        "catalyst_provider": score.catalyst_provider,
                        "catalysts": score.catalysts,
                    },
                    sort_keys=True,
                ),
                json.dumps(score.reasons),
                json.dumps(score.risks),
            )
            for score in scores
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO scores (
                    run_id,
                    symbol,
                    score,
                    action,
                    suggested_amount,
                    last_price,
                    metrics_json,
                    reasons_json,
                    risks_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def annotate_score_changes(
        self,
        scores: list[StockScore],
        *,
        is_shadow: bool,
    ) -> list[StockScore]:
        """Attach comparable-run movement without changing the deterministic score."""
        if not scores:
            return []
        with self.connect() as connection:
            previous_run = connection.execute(
                """
                SELECT r.id
                FROM runs r
                JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = ?
                  AND r.market_degraded = 0
                  AND EXISTS (SELECT 1 FROM scores s WHERE s.run_id = r.id)
                ORDER BY r.id DESC
                LIMIT 1
                """,
                (int(is_shadow),),
            ).fetchone()
            if previous_run is None:
                return [
                    replace(
                        score,
                        metrics={
                            **score.metrics,
                            "signal_state": "new_coverage",
                            "current_rank": rank,
                            "new_reasons": list(score.reasons[:3]),
                            "new_risks": list(score.risks[:2]),
                        },
                    )
                    for rank, score in enumerate(scores, start=1)
                ]
            previous_rows = connection.execute(
                """
                SELECT symbol, score, action, suggested_amount,
                       reasons_json, risks_json
                FROM scores
                WHERE run_id = ?
                ORDER BY score DESC, symbol
                """,
                (previous_run["id"],),
            ).fetchall()

        previous = {
            row["symbol"]: {
                "score": float(row["score"]),
                "action": row["action"],
                "is_candidate": float(row["suggested_amount"]) > 0,
                "rank": rank,
                "reasons": set(json.loads(row["reasons_json"] or "[]")),
                "risks": set(json.loads(row["risks_json"] or "[]")),
            }
            for rank, row in enumerate(previous_rows, start=1)
        }
        annotated: list[StockScore] = []
        for current_rank, score in enumerate(scores, start=1):
            prior = previous.get(score.symbol)
            if prior is None:
                state = "new_candidate" if score.is_alert else "new_coverage"
                metrics = {
                    **score.metrics,
                    "signal_state": state,
                    "current_rank": current_rank,
                    "new_reasons": list(score.reasons[:3]),
                    "new_risks": list(score.risks[:2]),
                }
            else:
                score_delta = round(score.score - prior["score"], 1)
                rank_delta = int(prior["rank"]) - current_rank
                if score.is_alert and not prior["is_candidate"]:
                    state = "new_candidate"
                elif not score.is_alert and prior["is_candidate"]:
                    state = "lost_candidate"
                elif score_delta >= 5 or rank_delta >= 10:
                    state = "upgraded"
                elif score_delta <= -5 or rank_delta <= -10:
                    state = "downgraded"
                else:
                    state = "steady"
                metrics = {
                    **score.metrics,
                    "signal_state": state,
                    "current_rank": current_rank,
                    "previous_rank": prior["rank"],
                    "rank_delta": rank_delta,
                    "previous_score": prior["score"],
                    "score_delta": score_delta,
                    "previous_action": prior["action"],
                    "new_reasons": [
                        item for item in score.reasons if item not in prior["reasons"]
                    ][:3],
                    "new_risks": [
                        item for item in score.risks if item not in prior["risks"]
                    ][:2],
                    "resolved_risks": [
                        item for item in prior["risks"] if item not in set(score.risks)
                    ][:2],
                }
            annotated.append(replace(score, metrics=metrics))
        return annotated

    def annotate_calibration_context(
        self,
        scores: list[StockScore],
        horizon_days: int = 3,
        episode_gap_hours: float = 36.0,
    ) -> list[StockScore]:
        if not scores:
            return []
        rows = [
            row
            for row in self.get_calibration_rows()
            if int(row["horizon_days"]) == horizon_days
        ]
        summary = summarize_episode_calibration(
            rows,
            episode_gap_hours=episode_gap_hours,
        )
        grouped = {
            (str(row["action"]), str(row["score_band"])): row
            for row in summary["action_score_band_summaries"]
            if int(row["horizon_days"]) == horizon_days
        }
        annotated: list[StockScore] = []
        for score in scores:
            bucket = grouped.get((score.action, _score_band_label(score.score)))
            sample_count = int(bucket["count"]) if bucket else 0
            calibration = {
                "calibration_horizon_days": horizon_days,
                "calibration_score_band": _score_band_label(score.score),
                "calibration_sample_count": sample_count,
                "calibration_sample_type": "episode_adjusted",
                "calibration_episode_gap_hours": episode_gap_hours,
                "calibration_confidence": _calibration_confidence(sample_count),
            }
            if bucket:
                calibration.update(
                    {
                        "calibration_win_rate_pct": float(bucket["win_rate_pct"]),
                        "calibration_median_return_pct": float(
                            bucket["median_return_pct"]
                        ),
                        "calibration_median_relative_return_pct": bucket[
                            "median_relative_return_pct"
                        ],
                        "calibration_average_adverse_pct": float(
                            bucket["average_max_adverse_pct"]
                        ),
                    }
                )
            annotated.append(
                replace(score, metrics={**score.metrics, **calibration})
            )
        return annotated

    def update_run_summary(self, run_id: int, scores: list[StockScore]) -> None:
        scores = [
            score
            for score in scores
            if score.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        top = scores[0] if scores else None
        alert_count = sum(1 for score in scores if score.is_alert)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET top_symbol = ?, top_score = ?, alert_count = ?
                WHERE id = ?
                """,
                (
                    top.symbol if top else None,
                    top.score if top else None,
                    alert_count,
                    run_id,
                ),
            )

    def update_run_notification_status(
        self,
        run_id: int,
        status: str,
        notification_format: str,
        message: str = "",
    ) -> None:
        clean_status = status.strip().lower()
        if clean_status not in {
            "delivered",
            "failed",
            "dry_run",
            "not_applicable",
            "unknown",
        }:
            raise ValueError("Invalid run notification status.")
        clean_format = notification_format.strip().lower()
        if clean_format not in {"pdf", "text_fallback", "text", "none", ""}:
            raise ValueError("Invalid run notification format.")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET notification_status = ?, notification_format = ?,
                    notification_message = ?, completed_at = COALESCE(completed_at, ?)
                WHERE id = ?
                """,
                (
                    clean_status,
                    clean_format,
                    message[:200],
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )

    def get_pending_outcome_scores(
        self,
        symbols: list[str],
        horizon_days: int,
        before: datetime,
    ) -> list[sqlite3.Row]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT s.run_id, s.symbol, s.last_price, s.action, r.started_at
                FROM scores s
                JOIN runs r ON r.id = s.run_id
                LEFT JOIN score_outcomes o
                  ON o.run_id = s.run_id
                 AND o.symbol = s.symbol
                 AND o.horizon_days = ?
                WHERE s.symbol IN ({placeholders})
                  AND r.started_at < ?
                  AND r.market_degraded = 0
                  AND o.run_id IS NULL
                ORDER BY r.started_at
                """,
                (horizon_days, *symbols, before.isoformat()),
            ).fetchall()

    def insert_score_outcomes(self, outcomes: list[object]) -> None:
        outcomes = [
            outcome
            for outcome in outcomes
            if outcome.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        if not outcomes:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO score_outcomes (
                    run_id, symbol, horizon_days, evaluated_at, entry_price,
                    exit_price, return_pct, benchmark_return_pct,
                    relative_return_pct, max_favorable_pct, max_adverse_pct
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        outcome.run_id,
                        outcome.symbol,
                        outcome.horizon_days,
                        outcome.evaluated_at.isoformat(),
                        outcome.entry_price,
                        outcome.exit_price,
                        outcome.return_pct,
                        outcome.benchmark_return_pct,
                        outcome.relative_return_pct,
                        outcome.max_favorable_pct,
                        outcome.max_adverse_pct,
                    )
                    for outcome in outcomes
                ],
            )

    def get_outcome_status(self) -> dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT o.horizon_days, s.action, o.return_pct,
                       o.relative_return_pct, o.max_adverse_pct
                FROM score_outcomes o
                JOIN scores s
                  ON s.run_id = o.run_id AND s.symbol = o.symbol
                ORDER BY o.horizon_days, s.action
                """
            ).fetchall()
        grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault((row["horizon_days"], row["action"]), []).append(row)
        summaries: list[dict[str, object]] = []
        for (horizon, action), items in sorted(grouped.items()):
            returns = sorted(float(item["return_pct"]) for item in items)
            relative = [
                float(item["relative_return_pct"])
                for item in items
                if item["relative_return_pct"] is not None
            ]
            summaries.append(
                {
                    "horizon_days": horizon,
                    "action": action,
                    "count": len(items),
                    "average_return_pct": round(sum(returns) / len(returns), 2),
                    "median_return_pct": round(_median(returns), 2),
                    "win_rate_pct": round(
                        sum(value > 0 for value in returns) / len(returns) * 100,
                        2,
                    ),
                    "average_relative_return_pct": round(
                        sum(relative) / len(relative), 2
                    )
                    if relative
                    else None,
                    "average_max_adverse_pct": round(
                        sum(float(item["max_adverse_pct"]) for item in items)
                        / len(items),
                        2,
                    ),
                }
            )
        return {"outcome_count": len(rows), "summaries": summaries}

    def get_calibration_rows(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT s.run_id, s.symbol, s.score, s.action, r.started_at,
                       o.horizon_days, o.return_pct, o.relative_return_pct,
                       o.max_adverse_pct
                FROM score_outcomes o
                JOIN scores s
                  ON s.run_id = o.run_id AND s.symbol = o.symbol
                JOIN runs r ON r.id = s.run_id
                WHERE r.market_degraded = 0
                ORDER BY s.symbol, r.started_at, o.horizon_days
                """
            ).fetchall()

    def create_portfolio_preview(
        self,
        statement_date: str,
        parser_version: str,
        positions: list[PortfolioPosition],
    ) -> tuple[int, PortfolioDiff]:
        positions = _exclude_portfolio_symbols(positions)
        _validate_portfolio_preview_input(statement_date, parser_version, positions)
        active_id = self.get_active_portfolio_import_id()
        active_positions = self.get_portfolio_positions(active_id) if active_id else []
        diff = _portfolio_diff(active_positions, positions)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO portfolio_imports (
                    created_at, statement_date, parser_version, status,
                    base_import_id
                )
                VALUES (?, ?, ?, 'preview', ?)
                """,
                (now, statement_date, parser_version, active_id),
            )
            import_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO portfolio_positions (
                    import_id, symbol, quantity, average_cost, classification
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        position.symbol,
                        position.quantity,
                        position.average_cost,
                        position.classification,
                    )
                    for position in positions
                ],
            )
        return import_id, diff

    def apply_portfolio_preview(self, import_id: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            preview = connection.execute(
                """
                SELECT status, base_import_id
                FROM portfolio_imports
                WHERE id = ?
                """,
                (import_id,),
            ).fetchone()
            if preview is None or preview["status"] != "preview":
                raise ValueError("Portfolio preview is not available for application.")
            active = connection.execute(
                """
                SELECT id
                FROM portfolio_imports
                WHERE status = 'active'
                ORDER BY applied_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            active_id = int(active["id"]) if active else None
            if active_id != preview["base_import_id"]:
                raise ValueError(
                    "Portfolio changed after this preview; create a fresh preview."
                )
            if active_id is not None:
                connection.execute(
                    """
                    UPDATE portfolio_imports
                    SET status = 'superseded'
                    WHERE id = ?
                    """,
                    (active_id,),
                )
            connection.execute(
                """
                UPDATE portfolio_imports
                SET status = 'active', applied_at = ?
                WHERE id = ?
                """,
                (now, import_id),
            )
        return import_id

    def get_active_portfolio_import_id(self) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM portfolio_imports
                WHERE status = 'active'
                ORDER BY applied_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return int(row["id"]) if row else None

    def get_portfolio_import(self, import_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, statement_date, parser_version, status,
                       base_import_id, applied_at
                FROM portfolio_imports
                WHERE id = ?
                """,
                (import_id,),
            ).fetchone()

    def get_portfolio_positions(
        self,
        import_id: int | None = None,
    ) -> list[PortfolioPosition]:
        target_id = import_id or self.get_active_portfolio_import_id()
        if target_id is None:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, quantity, average_cost, classification
                FROM portfolio_positions
                WHERE import_id = ?
                ORDER BY symbol
                """,
                (target_id,),
            ).fetchall()
        return [
            PortfolioPosition(
                symbol=row["symbol"],
                quantity=float(row["quantity"]),
                average_cost=float(row["average_cost"]),
                classification=row["classification"],
            )
            for row in rows
        ]

    def get_portfolio_policy(self, symbol: str) -> PortfolioPolicy:
        clean_symbol = symbol.upper()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT symbol, classification_override, concentration_exempt,
                       buy_more_enabled
                FROM portfolio_policies
                WHERE symbol = ?
                """,
                (clean_symbol,),
            ).fetchone()
        if row is None:
            return PortfolioPolicy(clean_symbol, None, False, True)
        return PortfolioPolicy(
            symbol=row["symbol"],
            classification_override=row["classification_override"],
            concentration_exempt=bool(row["concentration_exempt"]),
            buy_more_enabled=bool(row["buy_more_enabled"]),
        )

    def set_portfolio_policy(
        self,
        symbol: str,
        classification_override: str | None,
        concentration_exempt: bool,
        buy_more_enabled: bool,
    ) -> PortfolioPolicy:
        clean_symbol = symbol.upper()
        if clean_symbol in EXCLUDED_ANALYSIS_SYMBOLS:
            raise ValueError("Symbol is excluded by the portfolio privacy policy.")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_policies (
                    symbol, classification_override, concentration_exempt,
                    buy_more_enabled, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    classification_override = excluded.classification_override,
                    concentration_exempt = excluded.concentration_exempt,
                    buy_more_enabled = excluded.buy_more_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_symbol,
                    classification_override,
                    int(concentration_exempt),
                    int(buy_more_enabled),
                    now,
                ),
            )
        return self.get_portfolio_policy(clean_symbol)

    def create_portfolio_monitor_run(
        self,
        import_id: int,
        started_at: datetime,
        market_coverage_pct: float,
        market_degraded: bool,
        total_invested_value: float,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO portfolio_monitor_runs (
                    started_at, import_id, market_coverage_pct,
                    market_degraded, total_invested_value,
                    analysis_status, notification_status, completed_at
                )
                VALUES (?, ?, ?, ?, ?, 'completed', 'pending', ?)
                """,
                (
                    started_at.isoformat(),
                    import_id,
                    market_coverage_pct,
                    int(market_degraded),
                    total_invested_value,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def update_portfolio_notification_status(
        self,
        run_id: int,
        status: str,
        message: str = "",
        notification_format: str = "",
    ) -> None:
        clean_status = status.strip().lower()
        if clean_status not in {"delivered", "failed", "dry_run", "unknown"}:
            raise ValueError("Invalid portfolio notification status.")
        clean_format = notification_format.strip().lower()
        if clean_format not in {"pdf", "text_fallback", "text", "none", ""}:
            raise ValueError("Invalid portfolio notification format.")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE portfolio_monitor_runs
                SET notification_status = ?, notification_format = ?,
                    notification_message = ?
                WHERE id = ?
                """,
                (clean_status, clean_format, message[:200], run_id),
            )

    def insert_portfolio_assessments(
        self,
        run_id: int,
        assessments: list[PortfolioAssessment],
    ) -> None:
        assessments = [
            item
            for item in assessments
            if item.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO portfolio_assessments (
                    run_id, symbol, action, classification, current_price,
                    current_value, weight_pct, return_from_cost_pct,
                    daily_return_pct, return_5d_pct, score,
                    reasons_text, risks_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item.symbol,
                        item.action,
                        item.classification,
                        item.current_price,
                        item.current_value,
                        item.weight_pct,
                        item.return_from_cost_pct,
                        item.daily_return_pct,
                        item.return_5d_pct,
                        item.score,
                        " | ".join(item.reasons[:5]),
                        " | ".join(item.risks[:5]),
                    )
                    for item in assessments
                ],
            )
            connection.execute(
                """
                UPDATE portfolio_monitor_runs
                SET total_invested_value = COALESCE(
                    (
                        SELECT SUM(current_value)
                        FROM portfolio_assessments
                        WHERE run_id = ?
                    ),
                    0
                )
                WHERE id = ?
                """,
                (run_id, run_id),
            )

    def get_portfolio_status(self) -> dict[str, object]:
        active_id = self.get_active_portfolio_import_id()
        positions = self.get_portfolio_positions(active_id) if active_id else []
        with self.connect() as connection:
            latest_run = connection.execute(
                """
                SELECT id, started_at, market_coverage_pct, market_degraded,
                       total_invested_value, analysis_status,
                       notification_status, notification_format,
                       notification_message, completed_at
                FROM portfolio_monitor_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            actions = []
            if latest_run:
                actions = connection.execute(
                    """
                    SELECT action, COUNT(*) AS count
                    FROM portfolio_assessments
                    WHERE run_id = ?
                    GROUP BY action
                    ORDER BY action
                    """,
                    (latest_run["id"],),
                ).fetchall()
        return {
            "active_import_id": active_id,
            "position_count": len(positions),
            "latest_run": dict(latest_run) if latest_run else None,
            "actions": {row["action"]: row["count"] for row in actions},
        }

    def get_latest_portfolio_actions(self, import_id: int) -> dict[str, str]:
        with self.connect() as connection:
            latest_run = connection.execute(
                """
                SELECT id
                FROM portfolio_monitor_runs
                WHERE import_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (import_id,),
            ).fetchone()
            if latest_run is None:
                return {}
            rows = connection.execute(
                """
                SELECT symbol, action
                FROM portfolio_assessments
                WHERE run_id = ?
                ORDER BY symbol
                """,
                (latest_run["id"],),
            ).fetchall()
        return {row["symbol"]: row["action"] for row in rows}

    def get_portfolio_action_history(
        self,
        import_id: int,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                WITH recent_runs AS (
                    SELECT id, started_at, market_coverage_pct, market_degraded
                    FROM portfolio_monitor_runs
                    WHERE import_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                SELECT r.id AS run_id, r.started_at, r.market_coverage_pct,
                       r.market_degraded, a.symbol, a.action
                FROM recent_runs r
                JOIN portfolio_assessments a ON a.run_id = r.id
                ORDER BY r.id DESC, a.symbol
                """,
                (import_id, max(1, limit)),
            ).fetchall()

    def insert_portfolio_price_snapshots(
        self,
        snapshots: list[PortfolioPriceSnapshot],
    ) -> None:
        snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        if not snapshots:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO portfolio_price_snapshots (
                    captured_at, trade_date, symbol, quantity, price,
                    previous_close, baseline_price, move_pct, move_dollars,
                    position_value, day_dollar_change, source,
                    freshness_seconds, degraded, message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.captured_at.isoformat(),
                        snapshot.trade_date.isoformat(),
                        snapshot.symbol,
                        snapshot.quantity,
                        snapshot.price,
                        snapshot.previous_close,
                        snapshot.baseline_price,
                        snapshot.move_pct,
                        snapshot.move_dollars,
                        snapshot.position_value,
                        snapshot.day_dollar_change,
                        snapshot.source,
                        snapshot.freshness_seconds,
                        int(snapshot.degraded),
                        snapshot.message[:200],
                    )
                    for snapshot in snapshots
                ],
            )

    def get_latest_portfolio_price_snapshots(
        self,
        trade_date: date,
    ) -> dict[str, PortfolioPriceSnapshot]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM portfolio_price_snapshots
                WHERE trade_date = ?
                  AND symbol NOT IN ({})
                ORDER BY captured_at DESC, id DESC
                """.format(",".join("?" for _ in EXCLUDED_ANALYSIS_SYMBOLS)),
                (trade_date.isoformat(), *sorted(EXCLUDED_ANALYSIS_SYMBOLS)),
            ).fetchall()
        snapshots: dict[str, PortfolioPriceSnapshot] = {}
        for row in rows:
            if row["symbol"] in snapshots:
                continue
            snapshots[row["symbol"]] = _snapshot_from_row(row)
        return snapshots

    def get_sent_portfolio_price_alert_levels(
        self,
        trade_date: date,
    ) -> set[tuple[str, str, float]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, direction, threshold_pct
                FROM portfolio_price_alerts
                WHERE trade_date = ?
                  AND notification_status IN ('delivered', 'dry_run')
                """,
                (trade_date.isoformat(),),
            ).fetchall()
        return {
            (row["symbol"], row["direction"], float(row["threshold_pct"]))
            for row in rows
            if row["symbol"] not in EXCLUDED_ANALYSIS_SYMBOLS
        }

    def insert_portfolio_price_alert(
        self,
        alert: PortfolioPriceAlert,
        status: str,
        message: str = "",
    ) -> bool:
        if alert.symbol in EXCLUDED_ANALYSIS_SYMBOLS:
            return False
        clean_status = status.strip().lower()
        if clean_status not in {"delivered", "failed", "dry_run", "pending"}:
            raise ValueError("Invalid portfolio price alert status.")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO portfolio_price_alerts (
                    trade_date, symbol, direction, threshold_pct, triggered_at,
                    baseline_price, current_price, move_pct, move_dollars,
                    notification_status, notification_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol, direction, threshold_pct)
                DO UPDATE SET triggered_at = excluded.triggered_at,
                              baseline_price = excluded.baseline_price,
                              current_price = excluded.current_price,
                              move_pct = excluded.move_pct,
                              move_dollars = excluded.move_dollars,
                              notification_status = excluded.notification_status,
                              notification_message = excluded.notification_message
                WHERE portfolio_price_alerts.notification_status = 'failed'
                """,
                (
                    alert.trade_date.isoformat(),
                    alert.symbol,
                    alert.direction,
                    alert.threshold_pct,
                    alert.triggered_at.isoformat(),
                    alert.baseline_price,
                    alert.current_price,
                    alert.move_pct,
                    alert.move_dollars,
                    clean_status,
                    message[:200],
                ),
            )
            return cursor.rowcount > 0

    def get_latest_portfolio_price_snapshots_for_date(
        self,
        trade_date: date,
    ) -> list[PortfolioPriceSnapshot]:
        latest = self.get_latest_portfolio_price_snapshots(trade_date)
        return [latest[symbol] for symbol in sorted(latest)]

    def create_portfolio_eod_report(self, report: PortfolioEodReport) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO portfolio_eod_reports (
                    trade_date, run_at, total_value, start_value,
                    total_gain_dollars, total_loss_dollars,
                    net_change_dollars, net_change_pct, winner_count,
                    loser_count, flat_count, source, market_coverage_pct,
                    degraded, notification_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(trade_date)
                DO UPDATE SET run_at = excluded.run_at,
                              total_value = excluded.total_value,
                              start_value = excluded.start_value,
                              total_gain_dollars = excluded.total_gain_dollars,
                              total_loss_dollars = excluded.total_loss_dollars,
                              net_change_dollars = excluded.net_change_dollars,
                              net_change_pct = excluded.net_change_pct,
                              winner_count = excluded.winner_count,
                              loser_count = excluded.loser_count,
                              flat_count = excluded.flat_count,
                              source = excluded.source,
                              market_coverage_pct = excluded.market_coverage_pct,
                              degraded = excluded.degraded,
                              notification_status = 'pending',
                              notification_format = '',
                              notification_message = ''
                """,
                (
                    report.trade_date.isoformat(),
                    report.run_at.isoformat(),
                    report.total_value,
                    report.start_value,
                    report.total_gain_dollars,
                    report.total_loss_dollars,
                    report.net_change_dollars,
                    report.net_change_pct,
                    report.winner_count,
                    report.loser_count,
                    report.flat_count,
                    report.source,
                    report.market_coverage_pct,
                    int(report.degraded),
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id FROM portfolio_eod_reports WHERE trade_date = ?",
                (report.trade_date.isoformat(),),
            ).fetchone()
            return int(row["id"])

    def update_portfolio_eod_notification_status(
        self,
        report_id: int,
        status: str,
        message: str = "",
        notification_format: str = "",
    ) -> None:
        clean_status = status.strip().lower()
        if clean_status not in {"delivered", "failed", "dry_run", "unknown"}:
            raise ValueError("Invalid portfolio EOD notification status.")
        clean_format = notification_format.strip().lower()
        if clean_format not in {"pdf", "text_fallback", "text", "none", ""}:
            raise ValueError("Invalid portfolio EOD notification format.")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE portfolio_eod_reports
                SET notification_status = ?, notification_format = ?,
                    notification_message = ?
                WHERE id = ?
                """,
                (clean_status, clean_format, message[:200], report_id),
            )

    def get_provider_status_summary(self, days: int = 7) -> list[dict[str, object]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT provider,
                       COUNT(*) AS call_count,
                       SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_count,
                       SUM(CASE WHEN status = 'plan_limited' THEN 1 ELSE 0 END)
                           AS plan_limited_count,
                       MAX(called_at) AS last_called_at
                FROM provider_calls
                WHERE called_at >= ?
                GROUP BY provider
                ORDER BY provider
                """,
                (cutoff.isoformat(),),
            ).fetchall()
        return [
            {
                "provider": row["provider"],
                "call_count": int(row["call_count"] or 0),
                "success_rate_pct": round(
                    int(row["ok_count"] or 0) / int(row["call_count"] or 1) * 100,
                    2,
                ),
                "cache_rate_pct": round(
                    int(row["cache_count"] or 0) / int(row["call_count"] or 1) * 100,
                    2,
                ),
                "plan_limited_count": int(row["plan_limited_count"] or 0),
                "last_called_at": row["last_called_at"],
            }
            for row in rows
        ]

    def get_provider_cache(
        self,
        provider: str,
        cache_key: str,
        max_age_hours: float | None = None,
    ) -> tuple[object, datetime] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT fetched_at, payload_json
                FROM provider_cache
                WHERE provider = ? AND cache_key = ?
                """,
                (provider, cache_key),
            ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if max_age_hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            comparable = (
                fetched_at.replace(tzinfo=timezone.utc)
                if fetched_at.tzinfo is None
                else fetched_at.astimezone(timezone.utc)
            )
            if comparable < cutoff:
                return None
        return json.loads(row["payload_json"]), fetched_at

    def set_provider_cache(
        self,
        provider: str,
        cache_key: str,
        payload: object,
        fetched_at: datetime | None = None,
    ) -> None:
        upper_key = cache_key.upper()
        if any(symbol in upper_key for symbol in EXCLUDED_ANALYSIS_SYMBOLS):
            return
        payload = _sanitize_excluded_payload(payload)
        if payload is None:
            return
        timestamp = fetched_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_cache (provider, cache_key, fetched_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, cache_key)
                DO UPDATE SET fetched_at = excluded.fetched_at,
                              payload_json = excluded.payload_json
                """,
                (provider, cache_key, timestamp.isoformat(), json.dumps(payload)),
            )

    def record_provider_call(
        self,
        provider: str,
        endpoint: str,
        symbol: str | None,
        ok: bool,
        status: str,
        item_count: int = 0,
        cache_hit: bool = False,
        message: str = "",
        called_at: datetime | None = None,
    ) -> None:
        if symbol and symbol.upper() in EXCLUDED_ANALYSIS_SYMBOLS:
            return
        timestamp = called_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO provider_calls (
                    called_at, provider, endpoint, symbol, ok, status,
                    item_count, cache_hit, message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    provider,
                    endpoint,
                    symbol,
                    int(ok),
                    status,
                    item_count,
                    int(cache_hit),
                    message[:300],
                ),
            )
            if self._provider_call_capture is not None:
                self._provider_call_capture.append(int(cursor.lastrowid))

    def begin_provider_call_capture(self) -> None:
        if self._provider_call_capture is not None:
            raise RuntimeError("Provider-call capture is already active.")
        self._provider_call_capture = []

    def finish_provider_call_capture(self) -> list[int]:
        if self._provider_call_capture is None:
            raise RuntimeError("Provider-call capture is not active.")
        call_ids = self._provider_call_capture
        self._provider_call_capture = None
        return call_ids

    def attach_provider_calls_to_run(
        self,
        run_id: int,
        call_ids: list[int],
    ) -> None:
        if not call_ids:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                UPDATE provider_calls
                SET run_id = ?
                WHERE id = ?
                """,
                [(run_id, call_id) for call_id in call_ids],
            )

    def attach_provider_calls_to_portfolio_run(
        self,
        portfolio_run_id: int,
        call_ids: list[int],
    ) -> None:
        if not call_ids:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                UPDATE provider_calls
                SET portfolio_run_id = ?
                WHERE id = ?
                """,
                [(portfolio_run_id, call_id) for call_id in call_ids],
            )

    def count_provider_calls_since(
        self,
        provider: str,
        since: datetime,
    ) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM provider_calls
                WHERE provider = ? AND called_at >= ? AND cache_hit = 0
                """,
                (provider, since.isoformat()),
            ).fetchone()
        return int(row["count"]) if row else 0

    def insert_catalyst_details(
        self,
        run_id: int,
        signals: dict[str, object],
    ) -> None:
        contribution_rows: list[tuple[object, ...]] = []
        event_rows: list[tuple[object, ...]] = []
        fundamental_rows: list[tuple[object, ...]] = []
        for symbol, signal in signals.items():
            if symbol in EXCLUDED_ANALYSIS_SYMBOLS:
                continue
            provider = getattr(signal, "provider", "none")
            snapshot = getattr(signal, "fundamental_snapshot", None)
            if snapshot is not None:
                fundamental_rows.append(
                    (
                        run_id,
                        symbol,
                        getattr(snapshot, "provider", provider),
                        getattr(snapshot, "as_of").isoformat(),
                        json.dumps(
                            getattr(snapshot, "metrics", {}),
                            sort_keys=True,
                        ),
                        f"{getattr(snapshot, 'provider', provider)} fundamentals",
                    )
                )
            for contribution in getattr(signal, "contributions", []):
                contribution_rows.append(
                    (
                        run_id,
                        symbol,
                        provider,
                        contribution.category,
                        contribution.score_delta,
                        contribution.confidence,
                        contribution.source,
                        contribution.summary,
                        contribution.event_id,
                        json.dumps(contribution.metadata, sort_keys=True),
                    )
                )
            for item in getattr(signal, "news_items", []):
                event_rows.append(
                    (
                        run_id,
                        symbol,
                        provider,
                        item.cluster_id or item.fingerprint,
                        item.category,
                        item.headline,
                        item.source,
                        item.published_at.isoformat() if item.published_at else None,
                        item.url,
                        item.relevance,
                        item.sentiment,
                    )
                )
        with self.connect() as connection:
            if contribution_rows:
                connection.executemany(
                    """
                    INSERT INTO score_contributions (
                        run_id, symbol, provider, category, score_delta,
                        confidence, source, summary, event_id, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    contribution_rows,
                )
            if event_rows:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO normalized_events (
                        run_id, symbol, provider, event_id, category, headline,
                        source, published_at, url, relevance, sentiment
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    event_rows,
                )
            if fundamental_rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO fundamental_snapshots (
                        run_id, symbol, provider, as_of, metrics_json, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    fundamental_rows,
                )

    def record_catalyst_run(
        self,
        run_id: int,
        catalyst_provider: str,
        is_shadow: bool,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO catalyst_runs (
                    run_id, catalyst_provider, is_shadow
                )
                VALUES (?, ?, ?)
                """,
                (run_id, catalyst_provider, int(is_shadow)),
            )

    def mark_shadow_review(
        self,
        run_id: int,
        symbol: str,
        decision: str,
        notes: str = "",
    ) -> None:
        with self.connect() as connection:
            exists = connection.execute(
                """
                SELECT 1
                FROM catalyst_runs
                WHERE run_id = ? AND is_shadow = 1
                """,
                (run_id,),
            ).fetchone()
            if exists is None:
                raise ValueError(f"Run {run_id} is not a recorded shadow run.")
            connection.execute(
                """
                INSERT INTO shadow_reviews (
                    run_id, symbol, reviewed_at, decision, notes
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, symbol)
                DO UPDATE SET reviewed_at = excluded.reviewed_at,
                              decision = excluded.decision,
                              notes = excluded.notes
                """,
                (
                    run_id,
                    symbol.upper(),
                    datetime.now(timezone.utc).isoformat(),
                    decision,
                    notes[:1000],
                ),
            )

    def get_shadow_status(self, days: int = 7) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.connect() as connection:
            runs = connection.execute(
                """
                SELECT r.id, r.started_at
                FROM runs r
                JOIN catalyst_runs c ON c.run_id = r.id
                WHERE c.is_shadow = 1
                  AND r.started_at >= ?
                  AND r.market_degraded = 0
                  AND EXISTS (
                      SELECT 1
                      FROM scores s
                      WHERE s.run_id = r.id
                  )
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
                  AND EXISTS (
                      SELECT 1
                      FROM scores s
                      WHERE s.run_id = r.id
                  )
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
        provider_summaries = shadow_provider_summaries(remote_calls)
        positives = sorted(float(row["score_delta"]) for row in positive_rows)
        p95 = 0.0
        if positives:
            index = max(0, min(len(positives) - 1, int(0.95 * len(positives) + 0.999) - 1))
            p95 = positives[index]

        candidate_changes: list[tuple[int, str]] = []
        previous_by_symbol: dict[str, str] = {}
        for row in score_rows:
            previous = previous_by_symbol.get(row["symbol"])
            if row["action"] == "candidate" and previous not in {None, "candidate"}:
                candidate_changes.append((row["run_id"], row["symbol"]))
            previous_by_symbol[row["symbol"]] = row["action"]
        unreviewed = [change for change in candidate_changes if change not in reviewed]
        started_at = [datetime.fromisoformat(row["started_at"]) for row in runs]
        span_days = (
            (max(started_at) - min(started_at)).total_seconds() / 86400
            if len(started_at) >= 2
            else 0.0
        )
        status = {
            "window_days": days,
            "scan_count": len(runs),
            "span_days": round(span_days, 2),
            "remote_call_count": len(remote_calls),
            "provider_success_rate_pct": round(success_rate, 2),
            "provider_summaries": provider_summaries,
            "positive_contribution_p95": round(p95, 2),
            "duplicate_news_contributions": int(duplicates["count"]) if duplicates else 0,
            "candidate_state_changes": len(candidate_changes),
            "unreviewed_candidate_changes": unreviewed,
        }
        return {**status, "promotion_gate": shadow_promotion_gate(status)}

    def get_market_health_status(self, days: int = 7) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, started_at, market_requested, market_received,
                       market_coverage_pct, market_degraded, market_failures_json
                FROM runs
                WHERE started_at >= ? AND market_requested > 0
                ORDER BY started_at DESC
                """,
                (cutoff.isoformat(),),
            ).fetchall()
        coverages = [float(row["market_coverage_pct"]) for row in rows]
        degraded = [row for row in rows if row["market_degraded"]]
        latest_failures: list[str] = []
        if rows:
            try:
                parsed = json.loads(rows[0]["market_failures_json"] or "[]")
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                latest_failures = [str(symbol) for symbol in parsed[:20]]
        return {
            "window_days": days,
            "scan_count": len(rows),
            "degraded_scan_count": len(degraded),
            "healthy_scan_rate_pct": round(
                (len(rows) - len(degraded)) / len(rows) * 100 if rows else 100.0,
                2,
            ),
            "average_coverage_pct": round(
                sum(coverages) / len(coverages) if coverages else 100.0,
                2,
            ),
            "minimum_coverage_pct": round(min(coverages) if coverages else 100.0, 2),
            "latest_failures": latest_failures,
        }


def _snapshot_from_row(row: sqlite3.Row) -> PortfolioPriceSnapshot:
    return PortfolioPriceSnapshot(
        symbol=row["symbol"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
        trade_date=date.fromisoformat(row["trade_date"]),
        quantity=float(row["quantity"]),
        price=float(row["price"]),
        previous_close=float(row["previous_close"]),
        baseline_price=float(row["baseline_price"]),
        move_pct=float(row["move_pct"]),
        move_dollars=float(row["move_dollars"]),
        position_value=float(row["position_value"]),
        day_dollar_change=float(row["day_dollar_change"]),
        source=row["source"],
        freshness_seconds=(
            int(row["freshness_seconds"])
            if row["freshness_seconds"] is not None
            else None
        ),
        degraded=bool(row["degraded"]),
        message=row["message"],
    )


def shadow_provider_summaries(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["provider"]), []).append(row)
    summaries: list[dict[str, object]] = []
    for provider, items in sorted(grouped.items()):
        calls = len(items)
        successes = sum(int(row["ok"]) for row in items)
        plan_limited = sum(1 for row in items if row["status"] == "plan_limited")
        success_rate = successes / calls * 100 if calls else 100.0
        if plan_limited:
            activation_state = "blocked_by_plan_limits"
        elif success_rate >= SHADOW_GATE_MIN_PROVIDER_SUCCESS_PCT:
            activation_state = "access_reliable"
        else:
            activation_state = "needs_reliability"
        summaries.append(
            {
                "provider": provider,
                "remote_call_count": calls,
                "success_rate_pct": round(success_rate, 2),
                "plan_limited_count": plan_limited,
                "activation_state": activation_state,
            }
        )
    return summaries


def shadow_promotion_gate(status: dict[str, object]) -> dict[str, object]:
    criteria = [
        {
            "name": "seven_day_window",
            "passed": float(status["span_days"]) >= SHADOW_GATE_MIN_SPAN_DAYS,
            "detail": (
                f"{status['span_days']} / {SHADOW_GATE_MIN_SPAN_DAYS:g} elapsed days"
            ),
        },
        {
            "name": "twenty_scans",
            "passed": int(status["scan_count"]) >= SHADOW_GATE_MIN_SCANS,
            "detail": f"{status['scan_count']} / {SHADOW_GATE_MIN_SCANS} scans",
        },
        {
            "name": "provider_reliability",
            "passed": (
                float(status["provider_success_rate_pct"])
                >= SHADOW_GATE_MIN_PROVIDER_SUCCESS_PCT
            ),
            "detail": (
                f"{status['provider_success_rate_pct']}% / "
                f"{SHADOW_GATE_MIN_PROVIDER_SUCCESS_PCT:g}% success"
            ),
        },
        {
            "name": "contribution_cap",
            "passed": (
                float(status["positive_contribution_p95"])
                <= SHADOW_GATE_MAX_POSITIVE_P95
            ),
            "detail": (
                f"+{status['positive_contribution_p95']} / "
                f"+{SHADOW_GATE_MAX_POSITIVE_P95:g} p95 positive contribution"
            ),
        },
        {
            "name": "duplicate_news",
            "passed": int(status["duplicate_news_contributions"]) == 0,
            "detail": f"{status['duplicate_news_contributions']} duplicate scored stories",
        },
        {
            "name": "manual_transition_review",
            "passed": len(status["unreviewed_candidate_changes"]) == 0,
            "detail": (
                f"{len(status['unreviewed_candidate_changes'])} unreviewed "
                "candidate transitions"
            ),
        },
    ]
    blocked = [item for item in criteria if not item["passed"]]
    plan_limited = [
        row
        for row in status.get("provider_summaries", [])
        if row.get("plan_limited_count", 0)
    ]
    if plan_limited:
        criteria.append(
            {
                "name": "plan_limits",
                "passed": False,
                "detail": (
                    ", ".join(
                        f"{row['provider']}={row['plan_limited_count']}"
                        for row in plan_limited
                    )
                    + " plan-limited calls"
                ),
            }
        )
        blocked.append(criteria[-1])
    ready = not blocked
    return {
        "ready_for_manual_promotion": ready,
        "state": "ready_for_manual_promotion" if ready else "not_ready",
        "criteria": criteria,
        "blocked_reasons": [str(item["detail"]) for item in blocked],
        "policy": {
            "min_scans": SHADOW_GATE_MIN_SCANS,
            "min_span_days": SHADOW_GATE_MIN_SPAN_DAYS,
            "min_provider_success_pct": SHADOW_GATE_MIN_PROVIDER_SUCCESS_PCT,
            "max_positive_contribution_p95": SHADOW_GATE_MAX_POSITIVE_P95,
            "requires_zero_duplicate_news": True,
            "requires_manual_transition_review": True,
        },
    }


def _score_band_label(score: float) -> str:
    if score < 68:
        return "<68"
    if score < 78:
        return "68-77.9"
    if score < 85:
        return "78-84.9"
    return "85+"


def _calibration_confidence(sample_count: int) -> str:
    if sample_count >= 30:
        return "measured"
    if sample_count >= 10:
        return "early"
    if sample_count > 0:
        return "thin"
    return "unmeasured"
