from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS
from stock_analyzer.portfolio_models import (
    PortfolioEodReport,
    PortfolioPosition,
    PortfolioPriceAlert,
    PortfolioPriceSnapshot,
)


SWING_THRESHOLDS_PCT = (5.0, 10.0, 15.0)
MARKET_OPEN = time(6, 30)
MARKET_CLOSE = time(13, 0)
EOD_REPORT_TIME = time(13, 15)
MAX_REASONABLE_DAY_MOVE_PCT = 80.0
MAX_REASONABLE_PRIOR_SNAPSHOT_MOVE_PCT = 45.0
MAX_MARKET_HOURS_FRESHNESS_SECONDS = 45 * 60
MARKET_CLOSE_STALE_GRACE = time(12, 45)


def is_regular_market_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in _nyse_holidays(day.year)


def is_market_hours(now: datetime, timezone_name: str) -> bool:
    local = _localize(now, timezone_name)
    return (
        is_regular_market_day(local.date())
        and MARKET_OPEN <= local.time() <= MARKET_CLOSE
    )


def is_eod_report_window(now: datetime, timezone_name: str) -> bool:
    local = _localize(now, timezone_name)
    return is_regular_market_day(local.date()) and local.time() >= EOD_REPORT_TIME


def trade_date_for(now: datetime, timezone_name: str) -> date:
    return _localize(now, timezone_name).date()


def build_price_snapshots(
    positions: dict[str, PortfolioPosition],
    intraday_histories: dict[str, pd.DataFrame],
    daily_histories: dict[str, pd.DataFrame],
    run_at: datetime,
    *,
    source: str,
    previous_snapshots: dict[str, PortfolioPriceSnapshot] | None = None,
) -> list[PortfolioPriceSnapshot]:
    previous_snapshots = previous_snapshots or {}
    trade_date = run_at.date()
    snapshots: list[PortfolioPriceSnapshot] = []
    for symbol, position in sorted(positions.items()):
        if symbol in EXCLUDED_ANALYSIS_SYMBOLS:
            continue
        current_price, bar_time = _latest_price(intraday_histories.get(symbol))
        fallback_price, fallback_time = _latest_price(daily_histories.get(symbol))
        if current_price is None:
            current_price = fallback_price
            bar_time = fallback_time
        previous_close = _previous_close(daily_histories.get(symbol), trade_date)
        message = ""
        degraded = False
        if current_price is None or current_price <= 0:
            degraded = True
            current_price = 0.0
            message = "current price unavailable"
        if previous_close is None or previous_close <= 0:
            degraded = True
            previous_close = 0.0
            message = "previous close unavailable"

        baseline = previous_close
        move_pct = (
            (current_price / baseline - 1.0) * 100.0
            if baseline > 0 and current_price > 0
            else 0.0
        )
        move_dollars = current_price - baseline if baseline > 0 else 0.0
        prior = previous_snapshots.get(symbol)
        if (
            not degraded
            and prior is not None
            and prior.price > 0
            and abs((current_price / prior.price - 1.0) * 100.0)
            > MAX_REASONABLE_PRIOR_SNAPSHOT_MOVE_PCT
        ):
            degraded = True
            message = "price jump versus prior snapshot looks split/adjustment-like"
        if not degraded and abs(move_pct) > MAX_REASONABLE_DAY_MOVE_PCT:
            degraded = True
            message = "day move looks split/adjustment-like"
        if not degraded and not _fresh_enough(bar_time, run_at):
            degraded = True
            message = "price bar is stale for the current session"

        position_value = position.quantity * current_price
        day_dollar_change = position.quantity * move_dollars
        freshness = _freshness_seconds(bar_time, run_at)
        snapshots.append(
            PortfolioPriceSnapshot(
                symbol=symbol,
                captured_at=run_at,
                trade_date=trade_date,
                quantity=position.quantity,
                price=round(current_price, 4),
                previous_close=round(previous_close, 4),
                baseline_price=round(baseline, 4),
                move_pct=round(move_pct, 2),
                move_dollars=round(move_dollars, 4),
                position_value=round(position_value, 2),
                day_dollar_change=round(day_dollar_change, 2),
                source=source,
                freshness_seconds=freshness,
                degraded=degraded,
                message=message,
            )
        )
    return snapshots


def detect_price_alerts(
    snapshots: list[PortfolioPriceSnapshot],
    sent_levels: set[tuple[str, str, float]],
    triggered_at: datetime,
    thresholds: tuple[float, ...] = SWING_THRESHOLDS_PCT,
) -> list[PortfolioPriceAlert]:
    alerts: list[PortfolioPriceAlert] = []
    for snapshot in snapshots:
        if snapshot.degraded or snapshot.baseline_price <= 0:
            continue
        direction = "up" if snapshot.move_pct > 0 else "down"
        magnitude = abs(snapshot.move_pct)
        for threshold in thresholds:
            key = (snapshot.symbol, direction, threshold)
            if magnitude >= threshold and key not in sent_levels:
                alerts.append(
                    PortfolioPriceAlert(
                        symbol=snapshot.symbol,
                        trade_date=snapshot.trade_date,
                        direction=direction,
                        threshold_pct=threshold,
                        triggered_at=triggered_at,
                        baseline_price=snapshot.baseline_price,
                        current_price=snapshot.price,
                        move_pct=snapshot.move_pct,
                        move_dollars=snapshot.move_dollars,
                    )
                )
    return alerts


def group_alert_messages(
    snapshots: list[PortfolioPriceSnapshot],
    alerts: list[PortfolioPriceAlert],
    run_at: datetime,
) -> list[tuple[str, list[PortfolioPriceAlert], str]]:
    by_symbol: dict[str, list[PortfolioPriceAlert]] = {}
    snapshot_by_symbol = {snapshot.symbol: snapshot for snapshot in snapshots}
    for alert in alerts:
        by_symbol.setdefault(alert.symbol, []).append(alert)
    messages: list[tuple[str, list[PortfolioPriceAlert], str]] = []
    for symbol, symbol_alerts in sorted(by_symbol.items()):
        snapshot = snapshot_by_symbol[symbol]
        direction = "up" if snapshot.move_pct > 0 else "down"
        emoji = "🚀" if direction == "up" else "🔻"
        thresholds = ", ".join(
            f"{alert.threshold_pct:.0f}%"
            for alert in sorted(symbol_alerts, key=lambda item: item.threshold_pct)
        )
        verb = "up" if direction == "up" else "down"
        message = (
            f"{emoji} Portfolio price swing - {symbol}\n"
            f"{symbol} is {verb} {snapshot.move_pct:+.2f}% today "
            f"({snapshot.move_dollars:+.2f}/share), crossing {thresholds}.\n"
            f"Price ${snapshot.price:,.2f} vs prior close "
            f"${snapshot.previous_close:,.2f}; position impact "
            f"${snapshot.day_dollar_change:+,.2f}.\n"
            f"Source: {snapshot.source}; as of {run_at.strftime('%b %d, %I:%M %p %Z')}.\n"
            "Research only - no automatic trading."
        )
        messages.append((symbol, symbol_alerts, message))
    return messages


def build_eod_report(
    snapshots: list[PortfolioPriceSnapshot],
    run_at: datetime,
    *,
    source: str,
) -> PortfolioEodReport:
    valid = [snapshot for snapshot in snapshots if not snapshot.degraded]
    total_value = sum(snapshot.position_value for snapshot in valid)
    net = sum(snapshot.day_dollar_change for snapshot in valid)
    start_value = total_value - net
    total_gain = sum(max(snapshot.day_dollar_change, 0.0) for snapshot in valid)
    total_loss = sum(min(snapshot.day_dollar_change, 0.0) for snapshot in valid)
    winner_count = sum(1 for snapshot in valid if snapshot.day_dollar_change > 0)
    loser_count = sum(1 for snapshot in valid if snapshot.day_dollar_change < 0)
    flat_count = len(valid) - winner_count - loser_count
    coverage = len(valid) / len(snapshots) * 100.0 if snapshots else 0.0
    return PortfolioEodReport(
        trade_date=run_at.date(),
        run_at=run_at,
        total_value=round(total_value, 2),
        start_value=round(start_value, 2),
        total_gain_dollars=round(total_gain, 2),
        total_loss_dollars=round(total_loss, 2),
        net_change_dollars=round(net, 2),
        net_change_pct=round(net / start_value * 100.0, 2) if start_value > 0 else 0.0,
        winner_count=winner_count,
        loser_count=loser_count,
        flat_count=flat_count,
        source=source,
        market_coverage_pct=round(coverage, 2),
        degraded=coverage < 90.0 or len(valid) != len(snapshots),
        snapshots=snapshots,
    )


def eod_pdf_caption(report: PortfolioEodReport) -> str:
    valid = [snapshot for snapshot in report.snapshots if not snapshot.degraded]
    top_gainer = max(valid, key=lambda item: item.move_pct).symbol if valid else "n/a"
    top_loser = min(valid, key=lambda item: item.move_pct).symbol if valid else "n/a"
    return (
        f"EOD Portfolio - {report.run_at.strftime('%b %d, %I:%M %p %Z')} | "
        f"net ${report.net_change_dollars:+,.0f} ({report.net_change_pct:+.2f}%) | "
        f"top: {top_gainer} / {top_loser}"
    )


def eod_pdf_filename(run_at: datetime) -> str:
    timezone_label = "".join(
        character if character.isalnum() else "-"
        for character in (run_at.tzname() or "LOCAL")
    ).strip("-")
    return f"portfolio-eod-{run_at.strftime('%Y-%m-%d-%H%M')}-{timezone_label}.pdf"


def _latest_price(history: pd.DataFrame | None) -> tuple[float | None, datetime | None]:
    frame = _clean_history(history)
    if frame.empty:
        return None, None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return None, None
    index = close.index[-1]
    timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else None
    return float(close.iloc[-1]), timestamp


def _previous_close(history: pd.DataFrame | None, trade_date: date) -> float | None:
    frame = _clean_history(history)
    if frame.empty:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return None
    dated: list[tuple[date | None, float]] = []
    for index, value in close.items():
        item_date = index.date() if hasattr(index, "date") else None
        dated.append((item_date, float(value)))
    prior_values = [value for item_date, value in dated if item_date and item_date < trade_date]
    if prior_values:
        return prior_values[-1]
    if len(dated) >= 2:
        return dated[-2][1]
    return dated[-1][1]


def _clean_history(history: pd.DataFrame | None) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    frame = history.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "close" not in frame.columns:
        return pd.DataFrame()
    frame = frame.dropna(subset=["close"]).sort_index()
    frame = frame[pd.to_numeric(frame["close"], errors="coerce") > 0]
    return frame


def _freshness_seconds(bar_time: datetime | None, run_at: datetime) -> int | None:
    if bar_time is None:
        return None
    if bar_time.tzinfo is None:
        comparable = bar_time.replace(tzinfo=run_at.tzinfo)
    else:
        comparable = bar_time.astimezone(run_at.tzinfo)
    return max(0, int((run_at - comparable).total_seconds()))


def _fresh_enough(bar_time: datetime | None, run_at: datetime) -> bool:
    if bar_time is None:
        return False
    local_bar = bar_time.astimezone(run_at.tzinfo) if bar_time.tzinfo else bar_time.replace(tzinfo=run_at.tzinfo)
    local_run = run_at if run_at.tzinfo else run_at.replace(tzinfo=local_bar.tzinfo)
    if local_bar.date() != local_run.date():
        return False
    if MARKET_OPEN <= local_run.time() <= MARKET_CLOSE:
        return (
            0
            <= (local_run - local_bar).total_seconds()
            <= MAX_MARKET_HOURS_FRESHNESS_SECONDS
        )
    if local_run.time() > MARKET_CLOSE:
        return local_bar.time() >= MARKET_CLOSE_STALE_GRACE
    return False


def _localize(now: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def _nyse_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _good_friday(year),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    return {holiday for holiday in holidays if holiday.year == year}


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        day = date(year, 12, 31)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (day.weekday() - weekday) % 7
    return date(day.year, day.month, day.day - offset)


def _good_friday(year: int) -> date:
    # Anonymous Gregorian algorithm, then minus two days.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)
