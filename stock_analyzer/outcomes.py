from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


OUTCOME_HORIZONS = (1, 3, 5, 10, 21)
SCORE_BANDS = (
    (0.0, 68.0, "<68"),
    (68.0, 78.0, "68-77.9"),
    (78.0, 85.0, "78-84.9"),
    (85.0, float("inf"), "85+"),
)


@dataclass(frozen=True)
class ForwardOutcome:
    run_id: int
    symbol: str
    horizon_days: int
    evaluated_at: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    benchmark_return_pct: float | None
    relative_return_pct: float | None
    max_favorable_pct: float
    max_adverse_pct: float


def summarize_episode_calibration(
    rows: list[Any],
    episode_gap_hours: float = 36.0,
) -> dict[str, object]:
    representatives = _episode_representatives(rows, episode_gap_hours)
    return {
        "raw_observation_count": len(rows),
        "episode_observation_count": len(representatives),
        "action_summaries": _summarize_groups(
            representatives,
            lambda row: (int(row["horizon_days"]), str(row["action"])),
            ("horizon_days", "action"),
        ),
        "score_band_summaries": _summarize_groups(
            representatives,
            lambda row: (int(row["horizon_days"]), _score_band(float(row["score"]))),
            ("horizon_days", "score_band"),
        ),
        "action_score_band_summaries": _summarize_groups(
            representatives,
            lambda row: (
                int(row["horizon_days"]),
                (str(row["action"]), _score_band(float(row["score"]))),
            ),
            ("horizon_days", "action_score_band"),
        ),
    }


def compute_forward_outcome(
    row: Any,
    history: pd.DataFrame,
    benchmark_history: pd.DataFrame | None,
    horizon_days: int,
    evaluated_at: datetime,
) -> ForwardOutcome | None:
    closes = _close_series(history)
    if closes.empty:
        return None
    started_at = pd.Timestamp(row["started_at"])
    if started_at.tzinfo is not None:
        started_at = started_at.tz_convert(None)
    eligible = closes.index.normalize() <= started_at.normalize()
    if not eligible.any():
        return None
    entry_position = int(eligible.sum()) - 1
    exit_position = entry_position + horizon_days
    if exit_position >= len(closes):
        return None

    entry_price = float(row["last_price"])
    if entry_price <= 0:
        entry_price = float(closes.iloc[entry_position])
    exit_price = float(closes.iloc[exit_position])
    future = closes.iloc[entry_position + 1 : exit_position + 1]
    if future.empty or entry_price <= 0:
        return None

    benchmark_return = _benchmark_return(
        benchmark_history,
        started_at,
        horizon_days,
    )
    return_pct = (exit_price / entry_price - 1) * 100
    relative_return = (
        return_pct - benchmark_return if benchmark_return is not None else None
    )
    return ForwardOutcome(
        run_id=int(row["run_id"]),
        symbol=str(row["symbol"]),
        horizon_days=horizon_days,
        evaluated_at=evaluated_at,
        entry_price=entry_price,
        exit_price=exit_price,
        return_pct=return_pct,
        benchmark_return_pct=benchmark_return,
        relative_return_pct=relative_return,
        max_favorable_pct=max(
            0.0,
            (float(future.max()) / entry_price - 1) * 100,
        ),
        max_adverse_pct=min(
            0.0,
            (float(future.min()) / entry_price - 1) * 100,
        ),
    )


def _benchmark_return(
    history: pd.DataFrame | None,
    started_at: pd.Timestamp,
    horizon_days: int,
) -> float | None:
    if history is None:
        return None
    closes = _close_series(history)
    if closes.empty:
        return None
    eligible = closes.index.normalize() <= started_at.normalize()
    if not eligible.any():
        return None
    entry_position = int(eligible.sum()) - 1
    exit_position = entry_position + horizon_days
    if exit_position >= len(closes):
        return None
    entry = float(closes.iloc[entry_position])
    exit_price = float(closes.iloc[exit_position])
    return (exit_price / entry - 1) * 100 if entry > 0 else None


def _close_series(history: pd.DataFrame) -> pd.Series:
    if history.empty:
        return pd.Series(dtype=float)
    frame = history.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "close" not in frame.columns:
        return pd.Series(dtype=float)
    index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert(None)
    closes = pd.Series(
        pd.to_numeric(frame["close"], errors="coerce").to_numpy(),
        index=index,
        dtype=float,
    )
    return closes.dropna().sort_index()


def _episode_representatives(
    rows: list[Any],
    episode_gap_hours: float,
) -> list[Any]:
    gap = timedelta(hours=max(0.0, episode_gap_hours))
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row["symbol"]),
            datetime.fromisoformat(str(row["started_at"])),
            int(row["horizon_days"]),
        ),
    )
    previous: dict[str, tuple[datetime, str, int]] = {}
    next_episode_id = 0
    representatives: dict[tuple[int, int], Any] = {}
    for row in ordered:
        symbol = str(row["symbol"])
        action = str(row["action"])
        started_at = datetime.fromisoformat(str(row["started_at"]))
        prior = previous.get(symbol)
        if (
            prior is None
            or prior[1] != action
            or started_at - prior[0] > gap
        ):
            next_episode_id += 1
            episode_id = next_episode_id
        else:
            episode_id = prior[2]
        previous[symbol] = (started_at, action, episode_id)
        key = (episode_id, int(row["horizon_days"]))
        representatives.setdefault(key, row)
    return list(representatives.values())


def _summarize_groups(
    rows: list[Any],
    key_fn: Any,
    key_names: tuple[str, str],
) -> list[dict[str, object]]:
    grouped: dict[tuple[object, object], list[Any]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)
    summaries: list[dict[str, object]] = []
    for key, items in sorted(grouped.items(), key=lambda item: item[0]):
        returns = sorted(float(item["return_pct"]) for item in items)
        relative = [
            float(item["relative_return_pct"])
            for item in items
            if item["relative_return_pct"] is not None
        ]
        summaries.append(
            {
                key_names[0]: key[0],
                **_summary_key_payload(key_names[1], key[1]),
                "count": len(items),
                "average_return_pct": round(sum(returns) / len(returns), 2),
                "median_return_pct": round(_median(returns), 2),
                "win_rate_pct": round(
                    sum(value > 0 for value in returns) / len(returns) * 100,
                    2,
                ),
                "average_relative_return_pct": (
                    round(sum(relative) / len(relative), 2) if relative else None
                ),
                "median_relative_return_pct": (
                    round(_median(sorted(relative)), 2) if relative else None
                ),
                "average_max_adverse_pct": round(
                    sum(float(item["max_adverse_pct"]) for item in items)
                    / len(items),
                    2,
                ),
            }
        )
    return summaries


def _summary_key_payload(name: str, value: object) -> dict[str, object]:
    if name == "action_score_band":
        action, score_band = value
        return {"action": action, "score_band": score_band}
    return {name: value}


def _score_band(score: float) -> str:
    for lower, upper, label in SCORE_BANDS:
        if lower <= score < upper:
            return label
    return "unknown"


def _median(values: list[float]) -> float:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2
