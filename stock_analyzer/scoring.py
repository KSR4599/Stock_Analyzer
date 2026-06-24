from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from stock_analyzer.models import StockScore


MIN_HISTORY_DAYS = 90
MIN_PRICE = 2.0
MIN_AVG_DOLLAR_VOLUME = 5_000_000


def rank_symbols(
    histories: dict[str, pd.DataFrame],
    budget: float,
    alert_threshold: float,
    benchmark_symbol: str = "SPY",
    as_of: datetime | pd.Timestamp | None = None,
    excluded_symbols: set[str] | None = None,
) -> list[StockScore]:
    benchmark = histories.get(benchmark_symbol)
    excluded = {benchmark_symbol, *(excluded_symbols or set())}
    scores = [
        score_symbol(
            symbol=symbol,
            history=history,
            benchmark_history=benchmark,
            budget=budget,
            alert_threshold=alert_threshold,
            as_of=as_of,
        )
        for symbol, history in histories.items()
        if symbol not in excluded
    ]
    return sorted(scores, key=lambda item: item.score, reverse=True)


def score_symbol(
    symbol: str,
    history: pd.DataFrame,
    benchmark_history: pd.DataFrame | None,
    budget: float,
    alert_threshold: float,
    as_of: datetime | pd.Timestamp | None = None,
) -> StockScore:
    frame = _normalize_history(history)
    if frame.empty or len(frame) < MIN_HISTORY_DAYS:
        return StockScore(
            symbol=symbol,
            score=0.0,
            last_price=0.0,
            action="skip",
            suggested_amount=0.0,
            setup="insufficient data",
            risk_level="unknown",
            metrics={},
            risks=[f"Need at least {MIN_HISTORY_DAYS} trading days of price history."],
        )

    close = frame["close"].dropna()
    volume = frame["volume"].fillna(0)
    last_price = float(close.iloc[-1])

    benchmark_returns = _benchmark_returns(benchmark_history)
    metrics = _build_metrics(frame, benchmark_returns, as_of=as_of)
    component_scores = _component_scores(metrics)
    risk_penalty = _risk_penalty(metrics)
    cap = _score_cap(metrics)

    raw_score = sum(component_scores.values()) - risk_penalty
    score = round(float(np.clip(min(raw_score, cap), 0, 100)), 1)
    setup = _classify_setup(metrics)
    risk_level = _classify_risk(metrics)
    candidate_ok = _passes_candidate_gates(metrics)
    is_alert = score >= alert_threshold and candidate_ok
    action = "candidate" if is_alert else "watch" if score >= alert_threshold - 10 else "skip"
    suggested_amount = float(budget) if is_alert else 0.0

    metrics.update(
        {
            "score_momentum": _round_or_none(component_scores["momentum"]),
            "score_relative_strength": _round_or_none(component_scores["relative_strength"]),
            "score_trend": _round_or_none(component_scores["trend"]),
            "score_breakout": _round_or_none(component_scores["breakout"]),
            "score_volume": _round_or_none(component_scores["volume"]),
            "score_acceleration": _round_or_none(component_scores["acceleration"]),
            "risk_penalty": _round_or_none(risk_penalty),
            "score_cap": _round_or_none(cap),
        }
    )

    return StockScore(
        symbol=symbol,
        score=score,
        last_price=round(last_price, 2),
        action=action,
        suggested_amount=suggested_amount,
        setup=setup,
        risk_level=risk_level,
        metrics=metrics,
        reasons=_build_reasons(metrics, setup),
        risks=_build_risks(metrics, candidate_ok),
    )


def _normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()

    frame = history.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "close" not in frame.columns or "volume" not in frame.columns:
        return pd.DataFrame()

    columns = [column for column in ["open", "high", "low", "close", "volume"] if column in frame.columns]
    frame = frame[columns].dropna(subset=["close"]).sort_index()
    frame["volume"] = frame["volume"].fillna(0)
    return frame


def _build_metrics(
    frame: pd.DataFrame,
    benchmark_returns: dict[int, float | None],
    as_of: datetime | pd.Timestamp | None,
) -> dict[str, float | None]:
    close = frame["close"].dropna()
    volume = frame["volume"].fillna(0)
    last_price = float(close.iloc[-1])
    ema_10 = _ema(close, 10)
    ema_21 = _ema(close, 21)
    ema_50 = _ema(close, 50)
    ema_150 = _ema(close, 150)
    ema_200 = _ema(close, 200)
    ret_5d = _return_pct(close, 5)
    ret_10d = _return_pct(close, 10)
    ret_21d = _return_pct(close, 21)
    ret_63d = _return_pct(close, 63)
    ret_126d = _return_pct(close, 126)
    ret_252d = _return_pct(close, 252)

    relative_21d = _relative_return(ret_21d, benchmark_returns.get(21))
    relative_63d = _relative_return(ret_63d, benchmark_returns.get(63))
    relative_126d = _relative_return(ret_126d, benchmark_returns.get(126))

    high_20 = _rolling_high(close, 20)
    high_55 = _rolling_high(close, 55)
    high_252 = _rolling_high(close, 252)

    metrics = {
        "last_price": last_price,
        "last_bar_age_days": _last_bar_age_days(frame, as_of),
        "return_5d_pct": ret_5d,
        "return_10d_pct": ret_10d,
        "return_21d_pct": ret_21d,
        "return_63d_pct": ret_63d,
        "return_126d_pct": ret_126d,
        "return_252d_pct": ret_252d,
        "relative_to_spy_21d_pct": relative_21d,
        "relative_to_spy_63d_pct": relative_63d,
        "relative_to_spy_126d_pct": relative_126d,
        "drawdown_252d_pct": _drawdown_pct(close, 252),
        "volume_ratio_20d": _latest_volume_ratio(volume, 20),
        "volume_ratio_5d_vs_20d": _average_volume_ratio(volume, 5, 20),
        "up_down_volume_ratio_20d": _up_down_volume_ratio(close, volume, 20),
        "obv_trend_20d_pct": _obv_trend_pct(close, volume, 20),
        "avg_dollar_volume_20d": _avg_dollar_volume(close, volume, 20),
        "volatility_20d_annualized_pct": _annualized_volatility_pct(close, 20),
        "volatility_63d_annualized_pct": _annualized_volatility_pct(close, 63),
        "atr_14d_pct": _atr_pct(frame, 14),
        "max_abs_daily_return_20d_pct": _max_abs_daily_return_pct(close, 20),
        "ema_10": ema_10,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "ema_150": ema_150,
        "ema_200": ema_200,
        "ema_21_slope_10d_pct": _return_pct(_ema_series(close, 21), 10),
        "ema_50_slope_20d_pct": _return_pct(_ema_series(close, 50), 20),
        "distance_from_ema_21_pct": _distance_pct(last_price, ema_21),
        "distance_from_ema_50_pct": _distance_pct(last_price, ema_50),
        "distance_to_20d_high_pct": _distance_pct(last_price, high_20),
        "distance_to_55d_high_pct": _distance_pct(last_price, high_55),
        "distance_to_252d_high_pct": _distance_pct(last_price, high_252),
        "acceleration_5d_vs_21d_pct": _acceleration(ret_5d, ret_21d, 5, 21),
        "acceleration_21d_vs_63d_pct": _acceleration(ret_21d, ret_63d, 21, 63),
    }
    return {key: _round_or_none(value) for key, value in metrics.items()}


def _component_scores(metrics: dict[str, float | None]) -> dict[str, float]:
    momentum = (
        _scale(metrics.get("return_10d_pct"), -8, 18, 0, 5)
        + _scale(metrics.get("return_21d_pct"), -12, 35, 0, 9)
        + _scale(metrics.get("return_63d_pct"), -20, 85, 0, 10)
        + _scale(metrics.get("return_126d_pct"), -25, 125, 0, 4)
    )
    relative_strength = (
        _scale(metrics.get("relative_to_spy_21d_pct"), -8, 22, 0, 6)
        + _scale(metrics.get("relative_to_spy_63d_pct"), -15, 55, 0, 10)
        + _scale(metrics.get("relative_to_spy_126d_pct"), -20, 75, 0, 4)
    )
    trend = _trend_score(metrics)
    breakout = _breakout_score(metrics)
    volume = (
        _scale(metrics.get("volume_ratio_20d"), 0.7, 2.8, 0, 5)
        + _scale(metrics.get("volume_ratio_5d_vs_20d"), 0.75, 2.2, 0, 4)
        + _scale(metrics.get("up_down_volume_ratio_20d"), 0.7, 2.5, 0, 3)
        + _scale(metrics.get("obv_trend_20d_pct"), -20, 80, 0, 3)
    )
    acceleration = (
        _scale(metrics.get("acceleration_5d_vs_21d_pct"), -4, 8, 0, 4)
        + _scale(metrics.get("acceleration_21d_vs_63d_pct"), -8, 18, 0, 6)
    )

    return {
        "momentum": momentum,
        "relative_strength": relative_strength,
        "trend": trend,
        "breakout": breakout,
        "volume": volume,
        "acceleration": acceleration,
    }


def _trend_score(metrics: dict[str, float | None]) -> float:
    last_price = metrics.get("last_price")
    ema_10 = metrics.get("ema_10")
    ema_21 = metrics.get("ema_21")
    ema_50 = metrics.get("ema_50")
    ema_150 = metrics.get("ema_150")
    ema_200 = metrics.get("ema_200")
    score = 0.0

    if last_price is not None and ema_10 is not None and last_price > ema_10:
        score += 2
    if last_price is not None and ema_21 is not None and last_price > ema_21:
        score += 3
    if last_price is not None and ema_50 is not None and last_price > ema_50:
        score += 4
    if ema_21 is not None and ema_50 is not None and ema_21 > ema_50:
        score += 3
    if ema_50 is not None and ema_150 is not None and ema_50 > ema_150:
        score += 2
    if ema_150 is not None and ema_200 is not None and ema_150 > ema_200:
        score += 1
    if (metrics.get("ema_21_slope_10d_pct") or 0) > 0:
        score += 1.5
    if (metrics.get("ema_50_slope_20d_pct") or 0) > 0:
        score += 1.5

    return min(score, 18.0)


def _breakout_score(metrics: dict[str, float | None]) -> float:
    score = 0.0
    distance_20 = metrics.get("distance_to_20d_high_pct")
    distance_55 = metrics.get("distance_to_55d_high_pct")
    distance_252 = metrics.get("distance_to_252d_high_pct")
    ret_5d = metrics.get("return_5d_pct")
    volume_ratio = metrics.get("volume_ratio_20d")

    if distance_20 is not None:
        score += _scale(distance_20, -12, 0, 0, 4)
        if distance_20 >= -1.5:
            score += 2
    if distance_55 is not None:
        score += _scale(distance_55, -18, 0, 0, 4)
        if distance_55 >= -2:
            score += 2
    if distance_252 is not None:
        score += _scale(distance_252, -35, 0, 0, 2)
    if ret_5d is not None and ret_5d > 2 and volume_ratio is not None and volume_ratio >= 1.3:
        score += 3

    return min(score, 15.0)


def _risk_penalty(metrics: dict[str, float | None]) -> float:
    penalty = 0.0
    last_price = metrics.get("last_price")
    avg_dollar_volume = metrics.get("avg_dollar_volume_20d")
    drawdown = metrics.get("drawdown_252d_pct")
    volatility_20d = metrics.get("volatility_20d_annualized_pct")
    atr = metrics.get("atr_14d_pct")
    max_abs_day = metrics.get("max_abs_daily_return_20d_pct")
    distance_from_ema_21 = metrics.get("distance_from_ema_21_pct")
    last_bar_age_days = metrics.get("last_bar_age_days")

    if last_bar_age_days is not None and last_bar_age_days > 7:
        penalty += 35
    if last_price is not None and last_price < MIN_PRICE:
        penalty += 25
    if avg_dollar_volume is not None:
        if avg_dollar_volume < 1_000_000:
            penalty += 25
        elif avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
            penalty += 12
        elif avg_dollar_volume < 20_000_000:
            penalty += 4
    if drawdown is not None:
        if drawdown < -65:
            penalty += 18
        elif drawdown < -45:
            penalty += 10
        elif drawdown < -30:
            penalty += 4
    if volatility_20d is not None:
        if volatility_20d > 220:
            penalty += 18
        elif volatility_20d > 160:
            penalty += 10
        elif volatility_20d < 18:
            penalty += 4
    if atr is not None and atr > 14:
        penalty += 8
    if max_abs_day is not None and max_abs_day > 35:
        penalty += 10
    if distance_from_ema_21 is not None and distance_from_ema_21 > 35:
        penalty += 7

    return penalty


def _score_cap(metrics: dict[str, float | None]) -> float:
    cap = 100.0
    last_price = metrics.get("last_price")
    avg_dollar_volume = metrics.get("avg_dollar_volume_20d")
    ret_63d = metrics.get("return_63d_pct")
    relative_63d = metrics.get("relative_to_spy_63d_pct")
    distance_from_ema_50 = metrics.get("distance_from_ema_50_pct")
    volatility_20d = metrics.get("volatility_20d_annualized_pct")
    last_bar_age_days = metrics.get("last_bar_age_days")

    if last_bar_age_days is not None and last_bar_age_days > 7:
        cap = min(cap, 40)
    if last_price is not None and last_price < MIN_PRICE:
        cap = min(cap, 45)
    if avg_dollar_volume is not None and avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        cap = min(cap, 58)
    if ret_63d is not None and ret_63d <= 0:
        cap = min(cap, 62)
    if relative_63d is not None and relative_63d <= -5:
        cap = min(cap, 66)
    if distance_from_ema_50 is not None and distance_from_ema_50 < -5:
        cap = min(cap, 68)
    if volatility_20d is not None and volatility_20d > 240:
        cap = min(cap, 60)

    return cap


def _passes_candidate_gates(metrics: dict[str, float | None]) -> bool:
    last_price = metrics.get("last_price") or 0
    avg_dollar_volume = metrics.get("avg_dollar_volume_20d") or 0
    ret_5d = metrics.get("return_5d_pct") or 0
    ret_21d = metrics.get("return_21d_pct") or 0
    ret_63d = metrics.get("return_63d_pct") or 0
    relative_63d = metrics.get("relative_to_spy_63d_pct") or 0
    volume_ratio = metrics.get("volume_ratio_20d") or 0
    distance_20 = metrics.get("distance_to_20d_high_pct")
    distance_55 = metrics.get("distance_to_55d_high_pct")
    distance_from_ema_50 = metrics.get("distance_from_ema_50_pct")
    volatility_20d = metrics.get("volatility_20d_annualized_pct") or 0
    atr = metrics.get("atr_14d_pct") or 0
    max_abs_day = metrics.get("max_abs_daily_return_20d_pct") or 0
    last_bar_age_days = metrics.get("last_bar_age_days") or 0

    liquidity_ok = last_price >= MIN_PRICE and avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME
    fresh_data_ok = last_bar_age_days <= 7
    strength_ok = ret_63d >= 8 and relative_63d >= 0
    rapid_trigger = (
        ret_5d >= 3
        or ret_21d >= 8
        or volume_ratio >= 1.4
        or (distance_20 is not None and distance_20 >= -1.5)
        or (distance_55 is not None and distance_55 >= -2.0)
    )
    trend_ok = distance_from_ema_50 is None or distance_from_ema_50 >= -2.0
    risk_ok = volatility_20d <= 220 and atr <= 18 and max_abs_day <= 45

    return liquidity_ok and fresh_data_ok and strength_ok and rapid_trigger and trend_ok and risk_ok


def _classify_setup(metrics: dict[str, float | None]) -> str:
    distance_55 = metrics.get("distance_to_55d_high_pct")
    volume_ratio = metrics.get("volume_ratio_20d") or 0
    ret_21d = metrics.get("return_21d_pct") or 0
    ret_63d = metrics.get("return_63d_pct") or 0
    relative_63d = metrics.get("relative_to_spy_63d_pct") or 0
    acceleration = metrics.get("acceleration_21d_vs_63d_pct") or 0
    obv = metrics.get("obv_trend_20d_pct") or 0
    drawdown = metrics.get("drawdown_252d_pct")

    if distance_55 is not None and distance_55 >= -2 and volume_ratio >= 1.25:
        return "breakout momentum"
    if ret_63d >= 25 and relative_63d >= 12:
        return "relative strength leader"
    if ret_21d >= 8 and acceleration >= 4:
        return "rapid acceleration"
    if obv >= 35 and ret_21d > 0:
        return "accumulation surge"
    if drawdown is not None and -45 <= drawdown <= -12 and ret_21d >= 5:
        return "turnaround momentum"
    return "momentum watch"


def _classify_risk(metrics: dict[str, float | None]) -> str:
    points = 0
    volatility = metrics.get("volatility_20d_annualized_pct") or 0
    drawdown = metrics.get("drawdown_252d_pct") or 0
    atr = metrics.get("atr_14d_pct") or 0
    avg_dollar_volume = metrics.get("avg_dollar_volume_20d") or 0
    max_abs_day = metrics.get("max_abs_daily_return_20d_pct") or 0

    if volatility > 120:
        points += 2
    elif volatility > 75:
        points += 1
    if drawdown < -45:
        points += 2
    elif drawdown < -25:
        points += 1
    if atr > 10:
        points += 2
    elif atr > 6:
        points += 1
    if avg_dollar_volume < 20_000_000:
        points += 1
    if max_abs_day > 25:
        points += 1

    if points >= 5:
        return "speculative"
    if points >= 3:
        return "high"
    if points >= 1:
        return "medium"
    return "low"


def _benchmark_returns(benchmark_history: pd.DataFrame | None) -> dict[int, float | None]:
    if benchmark_history is None:
        return {}
    benchmark_frame = _normalize_history(benchmark_history)
    if benchmark_frame.empty:
        return {}
    close = benchmark_frame["close"].dropna()
    return {days: _return_pct(close, days) for days in [21, 63, 126]}


def _last_bar_age_days(
    frame: pd.DataFrame,
    as_of: datetime | pd.Timestamp | None,
) -> float | None:
    if frame.empty:
        return None

    try:
        last_bar = pd.Timestamp(frame.index[-1])
    except Exception:
        return None

    if pd.isna(last_bar):
        return None

    if as_of is None:
        as_of_timestamp = pd.Timestamp.now(tz=last_bar.tz)
    else:
        as_of_timestamp = pd.Timestamp(as_of)
        if last_bar.tz is not None and as_of_timestamp.tz is None:
            as_of_timestamp = as_of_timestamp.tz_localize(last_bar.tz)
        elif last_bar.tz is None and as_of_timestamp.tz is not None:
            as_of_timestamp = as_of_timestamp.tz_convert(None)

    return float((as_of_timestamp.normalize() - last_bar.normalize()).days)


def _return_pct(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    start = close.iloc[-days - 1]
    end = close.iloc[-1]
    if start == 0 or pd.isna(start) or pd.isna(end):
        return None
    return float((end / start - 1) * 100)


def _relative_return(stock_return: float | None, benchmark_return: float | None) -> float | None:
    if stock_return is None or benchmark_return is None:
        return None
    return stock_return - benchmark_return


def _drawdown_pct(close: pd.Series, days: int) -> float | None:
    window = close.tail(days)
    if window.empty:
        return None
    high = window.max()
    if high == 0 or pd.isna(high):
        return None
    return float((close.iloc[-1] / high - 1) * 100)


def _latest_volume_ratio(volume: pd.Series, days: int) -> float | None:
    if len(volume) <= days:
        return None
    latest = float(volume.iloc[-1])
    baseline = float(volume.tail(days + 1).iloc[:-1].replace(0, np.nan).mean())
    if baseline <= 0 or math.isnan(baseline):
        return None
    return latest / baseline


def _average_volume_ratio(volume: pd.Series, short_days: int, long_days: int) -> float | None:
    if len(volume) < short_days + long_days:
        return None
    short_volume = float(volume.tail(short_days).replace(0, np.nan).mean())
    long_volume = float(volume.tail(short_days + long_days).iloc[:-short_days].replace(0, np.nan).mean())
    if long_volume <= 0 or math.isnan(long_volume):
        return None
    return short_volume / long_volume


def _up_down_volume_ratio(close: pd.Series, volume: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    returns = close.pct_change().tail(days)
    recent_volume = volume.tail(days)
    up_volume = float(recent_volume[returns > 0].sum())
    down_volume = float(recent_volume[returns < 0].sum())
    if down_volume <= 0:
        return 3.0 if up_volume > 0 else None
    return up_volume / down_volume


def _obv_trend_pct(close: pd.Series, volume: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()
    baseline = float(volume.tail(days).sum())
    if baseline <= 0 or math.isnan(baseline):
        return None
    return float((obv.iloc[-1] - obv.iloc[-days - 1]) / baseline * 100)


def _avg_dollar_volume(close: pd.Series, volume: pd.Series, days: int) -> float | None:
    if len(close) < days:
        return None
    return float((close.tail(days) * volume.tail(days)).mean())


def _annualized_volatility_pct(close: pd.Series, days: int) -> float | None:
    returns = close.pct_change().dropna().tail(days)
    if len(returns) < 5:
        return None
    return float(returns.std() * math.sqrt(252) * 100)


def _atr_pct(frame: pd.DataFrame, days: int) -> float | None:
    if not {"high", "low", "close"}.issubset(frame.columns) or len(frame) < days + 1:
        return None
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.tail(days).mean())
    last_price = float(close.iloc[-1])
    if last_price <= 0 or math.isnan(atr):
        return None
    return atr / last_price * 100


def _max_abs_daily_return_pct(close: pd.Series, days: int) -> float | None:
    returns = close.pct_change().dropna().tail(days)
    if returns.empty:
        return None
    return float(returns.abs().max() * 100)


def _ema(close: pd.Series, days: int) -> float | None:
    series = _ema_series(close, days)
    if series.empty:
        return None
    return float(series.iloc[-1])


def _ema_series(close: pd.Series, days: int) -> pd.Series:
    if len(close) < days:
        return pd.Series(dtype=float)
    return close.ewm(span=days, adjust=False).mean()


def _rolling_high(close: pd.Series, days: int) -> float | None:
    if len(close) < days:
        return None
    return float(close.tail(days).max())


def _distance_pct(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0:
        return None
    return (value / reference - 1) * 100


def _acceleration(
    short_return: float | None,
    long_return: float | None,
    short_days: int,
    long_days: int,
) -> float | None:
    if short_return is None or long_return is None:
        return None
    return short_return - long_return * (short_days / long_days)


def _scale(
    value: float | None,
    in_min: float,
    in_max: float,
    out_min: float,
    out_max: float,
) -> float:
    if value is None or math.isnan(value):
        return 0.0
    clipped = min(max(value, in_min), in_max)
    ratio = (clipped - in_min) / (in_max - in_min)
    return out_min + ratio * (out_max - out_min)


def _round_or_none(value: float | None, digits: int = 2) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(float(value), digits)


def _build_reasons(metrics: dict[str, float | None], setup: str) -> list[str]:
    reasons: list[str] = [f"Setup: {setup}."]
    ret_21d = metrics.get("return_21d_pct")
    ret_63d = metrics.get("return_63d_pct")
    relative = metrics.get("relative_to_spy_63d_pct")
    volume_ratio = metrics.get("volume_ratio_20d")
    distance_55 = metrics.get("distance_to_55d_high_pct")
    acceleration = metrics.get("acceleration_21d_vs_63d_pct")

    if ret_21d is not None:
        reasons.append(f"1-month momentum is {ret_21d:+.1f}%.")
    if ret_63d is not None:
        reasons.append(f"3-month momentum is {ret_63d:+.1f}%.")
    if relative is not None:
        reasons.append(f"3-month relative strength vs SPY is {relative:+.1f}%.")
    if distance_55 is not None and distance_55 >= -3:
        reasons.append(f"Trading within {abs(distance_55):.1f}% of its 55-day high.")
    if volume_ratio is not None and volume_ratio >= 1.3:
        reasons.append(f"Latest volume is {volume_ratio:.1f}x the 20-day baseline.")
    if acceleration is not None and acceleration > 4:
        reasons.append(f"Recent momentum is accelerating by {acceleration:+.1f}%.")

    return reasons[:5] or ["No standout positive factor found yet."]


def _build_risks(metrics: dict[str, float | None], candidate_ok: bool) -> list[str]:
    risks: list[str] = []
    volatility = metrics.get("volatility_20d_annualized_pct")
    drawdown = metrics.get("drawdown_252d_pct")
    avg_dollar_volume = metrics.get("avg_dollar_volume_20d")
    atr = metrics.get("atr_14d_pct")
    max_abs_day = metrics.get("max_abs_daily_return_20d_pct")
    last_bar_age_days = metrics.get("last_bar_age_days")

    if last_bar_age_days is not None and last_bar_age_days > 7:
        risks.append(f"Latest price bar is stale by {last_bar_age_days:.0f} days.")
    if not candidate_ok:
        risks.append("Did not pass every liquidity, strength, trend, and risk gate for a $250 alert.")
    if avg_dollar_volume is not None and avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        risks.append(f"20-day dollar volume is thin at ${avg_dollar_volume:,.0f}.")
    if volatility is not None and volatility > 95:
        risks.append(f"High annualized 20-day volatility at {volatility:.1f}%.")
    if atr is not None and atr > 8:
        risks.append(f"Wide daily range: 14-day ATR is {atr:.1f}% of price.")
    if drawdown is not None and drawdown < -35:
        risks.append(f"Still {abs(drawdown):.1f}% below its 1-year high.")
    if max_abs_day is not None and max_abs_day > 25:
        risks.append(f"Recent single-day move reached {max_abs_day:.1f}%, so chase risk is elevated.")

    risks.append("Signal uses market data only; fundamentals/news/earnings catalysts are not in this pass.")
    return risks[:5]
