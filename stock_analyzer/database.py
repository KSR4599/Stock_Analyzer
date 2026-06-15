from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from stock_analyzer.models import StockScore


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    universe_source TEXT NOT NULL,
    universe_size INTEGER NOT NULL,
    top_symbol TEXT,
    top_score REAL,
    alert_count INTEGER DEFAULT 0
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
"""


class StockDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_run(
        self,
        started_at: datetime,
        provider: str,
        universe_source: str,
        universe_size: int,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (started_at, provider, universe_source, universe_size)
                VALUES (?, ?, ?, ?)
                """,
                (started_at.isoformat(), provider, universe_source, universe_size),
            )
            return int(cursor.lastrowid)

    def insert_scores(self, run_id: int, scores: list[StockScore]) -> None:
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

    def update_run_summary(self, run_id: int, scores: list[StockScore]) -> None:
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
