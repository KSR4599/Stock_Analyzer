from __future__ import annotations

import argparse
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analyzer.catalysts import (
    AlphaVantageCatalystProvider,
    AlphaVantageEndpointCheck,
    CompositeCatalystProvider,
    FinnhubCatalystProvider,
    FinnhubEndpointCheck,
    FmpCatalystProvider,
    FmpEndpointCheck,
    FredEndpointCheck,
    FredMarketContextProvider,
    MarketauxCatalystProvider,
    MarketauxEndpointCheck,
    NullCatalystProvider,
    SecEdgarCatalystProvider,
    apply_catalyst_signals,
    run_alpha_vantage_smoke_test,
    run_fred_smoke_test,
    run_finnhub_smoke_test,
    run_fmp_smoke_test,
    run_marketaux_smoke_test,
)
from stock_analyzer.catalysts.base import CatalystProvider
from stock_analyzer.config import Settings, clamp_alert_budget, load_settings
from stock_analyzer.database import StockDatabase
from stock_analyzer.dashboard import run_dashboard
from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS
from stock_analyzer.models import StockScore
from stock_analyzer.outcomes import (
    OUTCOME_HORIZONS,
    compute_forward_outcome,
    summarize_episode_calibration,
)
from stock_analyzer.pdf_reports import (
    build_portfolio_eod_pdf,
    build_portfolio_alert_pdf,
    build_universe_alert_pdf,
    portfolio_pdf_caption,
    portfolio_pdf_filename,
    universe_pdf_caption,
    universe_pdf_filename,
)
from stock_analyzer.portfolio_alerts import (
    build_eod_report,
    build_price_snapshots,
    detect_price_alerts,
    eod_pdf_caption,
    eod_pdf_filename,
    group_alert_messages,
    is_eod_report_window,
    is_market_hours,
    trade_date_for,
)
from stock_analyzer.portfolio import (
    assess_position,
    format_portfolio_report,
    portfolio_market_price,
)
from stock_analyzer.portfolio_pdf import (
    CSV_PARSER_VERSION,
    PARSER_VERSION,
    PortfolioImportError,
    parse_fidelity_positions_csv,
    parse_fidelity_positions_pdf,
)
from stock_analyzer.providers import DataProvider, YFinanceProvider
from stock_analyzer.reporting import format_error_alert, format_report
from stock_analyzer.scoring import rank_symbols
from stock_analyzer.telegram import (
    TelegramChat,
    TelegramConfigError,
    TelegramSendError,
    TelegramSender,
    fetch_recent_chat_ids,
)
from stock_analyzer.universe import build_universe


BENCHMARK_SYMBOL = "SPY"
MARKET_CONTEXT_SYMBOLS = ["SPY", "QQQ", "IWM", "SOXX"]
SHADOW_ONLY_CATALYST_PROVIDERS = {
    "alpha_vantage",
    "finnhub",
    "fred",
    "marketaux",
    "multi",
}


def build_provider(settings: Settings) -> DataProvider:
    if settings.provider == "yfinance":
        return YFinanceProvider(max_symbols_per_batch=settings.max_symbols_per_batch)
    raise ValueError(f"Unsupported provider: {settings.provider}")


def build_catalyst_provider(
    settings: Settings,
    state_store: StockDatabase | None = None,
) -> CatalystProvider:
    if settings.catalyst_provider in {"", "none", "off", "disabled"}:
        return NullCatalystProvider("Catalyst enrichment disabled by configuration.")
    if settings.catalyst_provider == "sec":
        return SecEdgarCatalystProvider(
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_days=settings.sec_lookback_days,
            max_filings=settings.sec_max_filings,
            state_store=state_store,
        )
    if settings.catalyst_provider == "fmp":
        if not settings.fmp_api_key:
            return NullCatalystProvider("FMP catalyst enrichment disabled because FMP_API_KEY is not set.")
        return FmpCatalystProvider(
            api_key=settings.fmp_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_hours=settings.catalyst_lookback_hours,
            max_news_articles=settings.catalyst_max_news_articles,
            state_store=state_store,
        )
    if settings.catalyst_provider == "finnhub":
        if not settings.finnhub_api_key:
            return NullCatalystProvider(
                "Finnhub catalyst enrichment disabled because FINNHUB_API_KEY is not set."
            )
        return FinnhubCatalystProvider(
            api_key=settings.finnhub_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_hours=settings.catalyst_lookback_hours,
            max_news_articles=settings.catalyst_max_news_articles,
            state_store=state_store,
        )
    if settings.catalyst_provider == "marketaux":
        if not settings.marketaux_api_token:
            return NullCatalystProvider(
                "Marketaux enrichment disabled because MARKETAUX_API_TOKEN is not set."
            )
        return MarketauxCatalystProvider(
            api_token=settings.marketaux_api_token,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_hours=settings.catalyst_lookback_hours,
            min_match_score=settings.marketaux_min_match_score,
            state_store=state_store,
        )
    if settings.catalyst_provider == "alpha_vantage":
        if not settings.alpha_vantage_api_key:
            return NullCatalystProvider(
                "Alpha Vantage enrichment disabled because ALPHA_VANTAGE_API_KEY is not set."
            )
        return AlphaVantageCatalystProvider(
            api_key=settings.alpha_vantage_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            state_store=state_store,
            daily_call_budget=settings.alpha_vantage_daily_call_budget,
        )
    if settings.catalyst_provider == "fred":
        if not settings.fred_api_key:
            return NullCatalystProvider(
                "FRED market context disabled because FRED_API_KEY is not set."
            )
        return FredMarketContextProvider(
            api_key=settings.fred_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            state_store=state_store,
        )
    if settings.catalyst_provider == "multi":
        providers: list[tuple[CatalystProvider, int | None]] = [
            (
                SecEdgarCatalystProvider(
                    user_agent=settings.sec_user_agent,
                    timeout_seconds=settings.request_timeout_seconds,
                    lookback_days=settings.sec_lookback_days,
                    max_filings=settings.sec_max_filings,
                    state_store=state_store,
                ),
                10,
            )
        ]
        if settings.finnhub_api_key:
            providers.append(
                (
                    FinnhubCatalystProvider(
                        api_key=settings.finnhub_api_key,
                        timeout_seconds=settings.request_timeout_seconds,
                        lookback_hours=settings.catalyst_lookback_hours,
                        max_news_articles=settings.catalyst_max_news_articles,
                        state_store=state_store,
                    ),
                    settings.finnhub_max_symbols_per_run,
                )
            )
        if settings.marketaux_api_token:
            providers.append(
                (
                    MarketauxCatalystProvider(
                        api_token=settings.marketaux_api_token,
                        timeout_seconds=settings.request_timeout_seconds,
                        lookback_hours=settings.catalyst_lookback_hours,
                        min_match_score=settings.marketaux_min_match_score,
                        state_store=state_store,
                    ),
                    settings.marketaux_max_symbols_per_run,
                )
            )
        if settings.alpha_vantage_api_key:
            providers.append(
                (
                    AlphaVantageCatalystProvider(
                        api_key=settings.alpha_vantage_api_key,
                        timeout_seconds=settings.request_timeout_seconds,
                        state_store=state_store,
                        daily_call_budget=settings.alpha_vantage_daily_call_budget,
                    ),
                    settings.alpha_vantage_max_symbols_per_run,
                )
            )
        if settings.fred_api_key:
            providers.append(
                (
                    FredMarketContextProvider(
                        api_key=settings.fred_api_key,
                        timeout_seconds=settings.request_timeout_seconds,
                        state_store=state_store,
                    ),
                    10,
                )
            )
        return CompositeCatalystProvider(providers)
    raise ValueError(f"Unsupported catalyst provider: {settings.catalyst_provider}")


def build_telegram_sender(settings: Settings) -> TelegramSender:
    return TelegramSender(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        dry_run=settings.dry_run,
        timeout_seconds=settings.request_timeout_seconds,
        allowed_chat_ids=settings.allowed_telegram_chat_ids,
    )


def run_once(settings: Settings) -> str:
    run_at = datetime.now(ZoneInfo(settings.timezone))
    telegram_sender = build_telegram_sender(settings)
    telegram_sender.validate_live_config()
    provider = build_provider(settings)
    database = StockDatabase(settings.db_path)
    database.initialize()
    catalyst_provider = build_catalyst_provider(settings, state_store=database)
    validate_catalyst_delivery_mode(settings)

    if settings.manual_symbols:
        universe = [
            symbol
            for symbol in _dedupe_symbols(settings.manual_symbols)
            if symbol not in EXCLUDED_ANALYSIS_SYMBOLS
        ]
        universe_source = "manual_symbols"
    else:
        universe, universe_source = build_universe(
            include_sp500=settings.include_sp500,
            extra_symbols=settings.extra_symbols,
            max_symbols=settings.max_symbols,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if not universe:
        raise ValueError("No symbols remain after applying analysis exclusions.")
    fetch_symbols = _with_market_context_symbols(universe)

    histories = provider.get_history(
        symbols=fetch_symbols,
        period=settings.history_period,
        interval=settings.history_interval,
    )
    market_received = sum(1 for symbol in universe if symbol in histories)
    market_coverage_pct = (
        market_received / len(universe) * 100 if universe else 100.0
    )
    market_failures = [symbol for symbol in universe if symbol not in histories]
    benchmark_available = BENCHMARK_SYMBOL in histories
    _update_forward_outcomes(database, histories, run_at)
    scores = rank_symbols(
        histories=histories,
        budget=settings.alert_budget,
        alert_threshold=settings.alert_score_threshold,
        benchmark_symbol=BENCHMARK_SYMBOL,
        as_of=run_at,
        excluded_symbols=set(MARKET_CONTEXT_SYMBOLS),
    )
    market_degraded = (
        market_coverage_pct < settings.min_market_coverage_pct
        or not benchmark_available
        or not scores
    )
    market_history_setter = getattr(catalyst_provider, "set_market_histories", None)
    if callable(market_history_setter):
        market_history_setter(histories)
    provider_call_ids: list[int] = []
    if catalyst_provider.name == "none" or market_degraded:
        enrichment_symbols = []
        catalyst_signals = {}
    else:
        enrichment_limit = settings.catalyst_top_n
        if catalyst_provider.name == "fmp":
            enrichment_limit = min(enrichment_limit, settings.fmp_max_symbols_per_run)
        elif catalyst_provider.name == "finnhub":
            enrichment_limit = min(enrichment_limit, settings.finnhub_max_symbols_per_run)
        elif catalyst_provider.name == "marketaux":
            enrichment_limit = min(enrichment_limit, settings.marketaux_max_symbols_per_run)
        elif catalyst_provider.name == "alpha_vantage":
            enrichment_limit = min(
                enrichment_limit,
                settings.alpha_vantage_max_symbols_per_run,
            )
        elif catalyst_provider.name == "multi":
            enrichment_limit = min(enrichment_limit, 10)
        enrichment_symbols = [score.symbol for score in scores[:enrichment_limit]]
        database.begin_provider_call_capture()
        try:
            catalyst_signals = catalyst_provider.fetch_signals(enrichment_symbols, run_at)
        finally:
            provider_call_ids = database.finish_provider_call_capture()
    scores = apply_catalyst_signals(
        scores=scores,
        signals=catalyst_signals,
        alert_threshold=settings.alert_score_threshold,
        budget=settings.alert_budget,
    )
    if market_degraded:
        scores = _suppress_candidate_alerts(scores, settings.alert_score_threshold)
    scores = database.annotate_score_changes(
        scores,
        is_shadow=catalyst_provider.name in SHADOW_ONLY_CATALYST_PROVIDERS,
    )
    scores = database.annotate_calibration_context(scores)

    run_id = database.create_run(
        started_at=run_at,
        provider=provider.name,
        universe_source=universe_source,
        universe_size=len(universe),
        market_requested=len(universe),
        market_received=market_received,
        market_coverage_pct=market_coverage_pct,
        market_degraded=market_degraded,
        market_failures=market_failures,
    )
    database.insert_scores(run_id=run_id, scores=scores)
    database.insert_catalyst_details(run_id=run_id, signals=catalyst_signals)
    database.record_catalyst_run(
        run_id=run_id,
        catalyst_provider=catalyst_provider.name,
        is_shadow=catalyst_provider.name in SHADOW_ONLY_CATALYST_PROVIDERS,
    )
    database.attach_provider_calls_to_run(run_id, provider_call_ids)
    database.update_run_summary(run_id=run_id, scores=scores)

    report_kind = "candidate_alert" if any(score.is_alert for score in scores) else "scheduled_report"
    report = format_report(
        scores=scores,
        run_at=run_at,
        provider=provider.name,
        catalyst_provider=catalyst_provider.name,
        catalyst_top_n=len(enrichment_symbols),
        universe_source=universe_source,
        universe_size=len(universe),
        budget=settings.alert_budget,
        threshold=settings.alert_score_threshold,
        top_n=settings.top_n,
        send_only_alerts=settings.send_only_alerts,
        report_kind=report_kind,
        market_requested=len(universe),
        market_received=market_received,
        market_coverage_pct=market_coverage_pct,
        market_degraded=market_degraded,
        market_failures=market_failures,
    )
    if catalyst_provider.name in SHADOW_ONLY_CATALYST_PROVIDERS:
        telegram_sender.send(report, message_kind=report_kind)
        database.update_run_notification_status(
            run_id,
            "not_applicable",
            "none",
            "Shadow runs do not send Telegram PDFs.",
        )
        return report

    caption = universe_pdf_caption(scores, run_at)
    try:
        pdf_bytes = build_universe_alert_pdf(
            scores=scores,
            run_at=run_at,
            provider=provider.name,
            catalyst_provider=catalyst_provider.name,
            universe_source=universe_source,
            universe_size=len(universe),
            budget=settings.alert_budget,
            threshold=settings.alert_score_threshold,
            market_requested=len(universe),
            market_received=market_received,
            market_coverage_pct=market_coverage_pct,
            market_degraded=market_degraded,
            market_failures=market_failures,
            top_n=settings.top_n,
        )
    except Exception as exc:
        fallback = (
            f"{caption}\nPDF generation failed ({type(exc).__name__}). "
            "Open the local dashboard for the complete report."
        )
        try:
            telegram_sender.send(fallback, message_kind="universe_pdf_fallback")
        except TelegramSendError as send_exc:
            database.update_run_notification_status(
                run_id,
                "failed",
                "text_fallback",
                str(send_exc),
            )
            raise
        database.update_run_notification_status(
            run_id,
            "dry_run" if settings.dry_run else "delivered",
            "text_fallback",
            f"PDF generation failed: {type(exc).__name__}",
        )
    else:
        try:
            telegram_sender.send_document(
                pdf_bytes,
                universe_pdf_filename(run_at),
                caption,
                "universe_pdf",
            )
        except TelegramSendError as exc:
            database.update_run_notification_status(
                run_id,
                "failed",
                "pdf",
                str(exc),
            )
            raise
        database.update_run_notification_status(
            run_id,
            "dry_run" if settings.dry_run else "delivered",
            "pdf",
        )
    return report


def schedule(settings: Settings) -> None:
    interval_seconds = settings.interval_hours * 60 * 60
    while True:
        try:
            run_once(settings)
        except Exception as exc:
            run_at = datetime.now(ZoneInfo(settings.timezone))
            error_message = format_error_alert(error=exc, run_at=run_at)
            try:
                build_telegram_sender(settings).send(error_message, message_kind="error_alert")
            except Exception as send_exc:
                print(f"Failed to send error alert: {type(send_exc).__name__}")
        time.sleep(interval_seconds)


def initialize_database(settings: Settings) -> None:
    StockDatabase(settings.db_path).initialize()
    print(f"Initialized database at {settings.db_path}")


def send_telegram_test(settings: Settings) -> str:
    run_at = datetime.now(ZoneInfo(settings.timezone))
    message = "\n".join(
        [
            f"Stock Analyzer Telegram test - {run_at.strftime('%Y-%m-%d %H:%M %Z')}",
            "This is a single safe test message.",
            "No market scan was run and no trade action is enabled.",
        ]
    )
    build_telegram_sender(settings).send(message, message_kind="telegram_test")
    return message


def print_telegram_chat_ids(settings: Settings) -> list[TelegramChat]:
    chats = fetch_recent_chat_ids(
        bot_token=settings.telegram_bot_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not chats:
        print("No Telegram chats found. Open your bot in Telegram, send it a message, then rerun this command.")
        return chats

    print("Recent Telegram chat IDs:")
    for chat in chats:
        print(f"- {chat.chat_id} | {chat.chat_type} | {chat.display_name}")
    print("")
    print("Set TELEGRAM_CHAT_ID to the desired ID, and include the same value in ALLOWED_TELEGRAM_CHAT_IDS.")
    return chats


def run_fmp_test(settings: Settings, symbol: str) -> list[FmpEndpointCheck]:
    if not settings.fmp_api_key:
        raise SystemExit("FMP_API_KEY is required. Add it to .env before running fmp-test.")

    symbols = _dedupe_symbols([symbol])
    if not symbols:
        raise SystemExit("A non-empty --symbol is required for fmp-test.")
    clean_symbol = symbols[0]
    checks = run_fmp_smoke_test(
        api_key=settings.fmp_api_key,
        symbol=clean_symbol,
        timeout_seconds=settings.request_timeout_seconds,
    )
    print(f"FMP smoke test for {clean_symbol}")
    print("Calls used: 5")
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"- {check.name}: {status} ({check.item_count} item(s)) {check.message}")
    if not all(check.ok for check in checks):
        raise SystemExit("FMP smoke test failed. Check plan access, rate limits, and API key.")
    return checks


def run_finnhub_test(settings: Settings, symbol: str) -> list[FinnhubEndpointCheck]:
    if not settings.finnhub_api_key:
        raise SystemExit(
            "FINNHUB_API_KEY is required. Add it to .env before running finnhub-test."
        )

    symbols = _dedupe_symbols([symbol])
    if not symbols:
        raise SystemExit("A non-empty --symbol is required for finnhub-test.")
    clean_symbol = symbols[0]
    checks = run_finnhub_smoke_test(
        api_key=settings.finnhub_api_key,
        symbol=clean_symbol,
        timeout_seconds=settings.request_timeout_seconds,
    )
    print(f"Finnhub smoke test for {clean_symbol}")
    print("Calls used: 5")
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"- {check.name}: {status} ({check.item_count} item(s)) {check.message}")
    if not all(check.ok for check in checks):
        raise SystemExit(
            "Finnhub smoke test had endpoint failures. Review plan access, rate limits, and API key."
        )
    return checks


def run_marketaux_test(
    settings: Settings,
    symbol: str,
) -> list[MarketauxEndpointCheck]:
    if not settings.marketaux_api_token:
        raise SystemExit(
            "MARKETAUX_API_TOKEN is required. Add it to .env before running marketaux-test."
        )
    clean_symbol = _required_symbol(symbol, "marketaux-test")
    checks = run_marketaux_smoke_test(
        api_token=settings.marketaux_api_token,
        symbol=clean_symbol,
        timeout_seconds=settings.request_timeout_seconds,
    )
    _print_checks("Marketaux", clean_symbol, checks, calls=1)
    if not all(check.ok for check in checks):
        raise SystemExit("Marketaux smoke test failed. Review quota and API token.")
    return checks


def run_alpha_vantage_test(
    settings: Settings,
    symbol: str,
) -> list[AlphaVantageEndpointCheck]:
    if not settings.alpha_vantage_api_key:
        raise SystemExit(
            "ALPHA_VANTAGE_API_KEY is required. Add it to .env before running alpha-vantage-test."
        )
    clean_symbol = _required_symbol(symbol, "alpha-vantage-test")
    checks = run_alpha_vantage_smoke_test(
        api_key=settings.alpha_vantage_api_key,
        symbol=clean_symbol,
        timeout_seconds=settings.request_timeout_seconds,
    )
    _print_checks("Alpha Vantage", clean_symbol, checks, calls=2)
    if not all(check.ok for check in checks):
        raise SystemExit("Alpha Vantage smoke test failed. Review quota and API key.")
    return checks


def run_fred_test(settings: Settings) -> list[FredEndpointCheck]:
    if not settings.fred_api_key:
        raise SystemExit("FRED_API_KEY is required. Add it to .env before running fred-test.")
    checks = run_fred_smoke_test(
        api_key=settings.fred_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    _print_checks("FRED", "market context", checks, calls=5)
    if not all(check.ok for check in checks):
        raise SystemExit("FRED smoke test failed. Review API key and series access.")
    return checks


def print_shadow_status(settings: Settings, days: int) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    status = database.get_shadow_status(days=days)
    print(f"Shadow evaluation window: {status['window_days']} day(s)")
    print(f"Scans: {status['scan_count']} | Span: {status['span_days']} day(s)")
    print(
        "Provider calls: "
        f"{status['remote_call_count']} | Success: "
        f"{status['provider_success_rate_pct']:.2f}%"
    )
    print(
        "Positive contribution p95: "
        f"{status['positive_contribution_p95']:+.2f}"
    )
    print(
        "Duplicate scored news: "
        f"{status['duplicate_news_contributions']}"
    )
    print(
        "Candidate changes: "
        f"{status['candidate_state_changes']} | Unreviewed: "
        f"{len(status['unreviewed_candidate_changes'])}"
    )
    gate = status["promotion_gate"]
    print(
        "Promotion gate: "
        f"{gate['state']} | ready={gate['ready_for_manual_promotion']}"
    )
    for criterion in gate["criteria"]:
        marker = "pass" if criterion["passed"] else "block"
        print(f"- {marker}: {criterion['name']} ({criterion['detail']})")
    if status["provider_summaries"]:
        print("Provider gate details:")
        for provider in status["provider_summaries"]:
            print(
                f"- {provider['provider']}: "
                f"{provider['success_rate_pct']:.2f}% success, "
                f"{provider['remote_call_count']} remote calls, "
                f"{provider['activation_state']}"
            )
    for run_id, symbol in status["unreviewed_candidate_changes"]:
        print(f"- review needed: run={run_id} symbol={symbol}")
    return status


def print_market_health(settings: Settings, days: int) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    status = database.get_market_health_status(days=days)
    print(f"Market health window: {status['window_days']} day(s)")
    print(
        f"Scans: {status['scan_count']} | Degraded: "
        f"{status['degraded_scan_count']} | Healthy: "
        f"{status['healthy_scan_rate_pct']:.2f}%"
    )
    print(
        f"Coverage average: {status['average_coverage_pct']:.2f}% | "
        f"Minimum: {status['minimum_coverage_pct']:.2f}%"
    )
    if status["latest_failures"]:
        print(f"Latest missing symbols: {', '.join(status['latest_failures'])}")
    return status


def print_outcome_status(settings: Settings) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    status = database.get_outcome_status()
    print(f"Forward outcomes recorded: {status['outcome_count']}")
    if not status["summaries"]:
        print("No horizons have matured yet.")
        return status
    for summary in status["summaries"]:
        relative = summary["average_relative_return_pct"]
        relative_text = f"{relative:+.2f}%" if relative is not None else "n/a"
        print(
            f"{summary['horizon_days']:>2}d {summary['action']:<9} "
            f"n={summary['count']:<4} avg={summary['average_return_pct']:+.2f}% "
            f"median={summary['median_return_pct']:+.2f}% "
            f"win={summary['win_rate_pct']:.1f}% "
            f"vs SPY={relative_text} "
            f"avg adverse={summary['average_max_adverse_pct']:+.2f}%"
        )
    return status


def print_calibration_status(
    settings: Settings,
    episode_gap_hours: float,
) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    status = summarize_episode_calibration(
        database.get_calibration_rows(),
        episode_gap_hours=episode_gap_hours,
    )
    print(
        "Calibration observations: "
        f"raw={status['raw_observation_count']} "
        f"episode-adjusted={status['episode_observation_count']} "
        f"gap={episode_gap_hours:g}h"
    )
    print("By action:")
    for summary in status["action_summaries"]:
        _print_calibration_summary(summary, "action")
    print("By score band:")
    for summary in status["score_band_summaries"]:
        _print_calibration_summary(summary, "score_band")
    return status


def _print_calibration_summary(summary: dict[str, object], label_key: str) -> None:
    relative = summary["average_relative_return_pct"]
    relative_text = f"{relative:+.2f}%" if relative is not None else "n/a"
    print(
        f"{summary['horizon_days']:>2}d {str(summary[label_key]):<9} "
        f"episodes={summary['count']:<3} "
        f"avg={summary['average_return_pct']:+.2f}% "
        f"median={summary['median_return_pct']:+.2f}% "
        f"win={summary['win_rate_pct']:.1f}% "
        f"vs SPY={relative_text} "
        f"avg adverse={summary['average_max_adverse_pct']:+.2f}%"
    )


def _update_forward_outcomes(
    database: StockDatabase,
    histories: dict[str, object],
    run_at: datetime,
) -> int:
    benchmark_history = histories.get(BENCHMARK_SYMBOL)
    outcomes = []
    symbols = [symbol for symbol in histories if symbol not in MARKET_CONTEXT_SYMBOLS]
    for horizon in OUTCOME_HORIZONS:
        for row in database.get_pending_outcome_scores(symbols, horizon, before=run_at):
            outcome = compute_forward_outcome(
                row=row,
                history=histories[row["symbol"]],
                benchmark_history=benchmark_history,
                horizon_days=horizon,
                evaluated_at=run_at,
            )
            if outcome is not None:
                outcomes.append(outcome)
    database.insert_score_outcomes(outcomes)
    return len(outcomes)


def record_shadow_review(
    settings: Settings,
    run_id: int,
    symbol: str,
    decision: str,
    notes: str,
) -> None:
    database = StockDatabase(settings.db_path)
    database.initialize()
    clean_symbol = _required_symbol(symbol, "shadow-review")
    database.mark_shadow_review(run_id, clean_symbol, decision, notes)
    print(f"Recorded shadow review for run {run_id}, {clean_symbol}: {decision}")


def preview_portfolio_import(
    settings: Settings,
    *,
    pdf_path: str | None = None,
    csv_path: str | None = None,
) -> int:
    database = StockDatabase(settings.db_path)
    database.initialize()
    if bool(pdf_path) == bool(csv_path):
        raise ValueError("Provide exactly one of --pdf or --csv.")
    if csv_path:
        parsed = parse_fidelity_positions_csv(Path(csv_path))
        parser_version = CSV_PARSER_VERSION
    else:
        parsed = parse_fidelity_positions_pdf(Path(pdf_path or ""))
        parser_version = PARSER_VERSION
    import_id, diff = database.create_portfolio_preview(
        statement_date=parsed.statement_date.isoformat(),
        parser_version=parser_version,
        positions=parsed.positions,
    )
    print(
        f"Portfolio preview {import_id}: {len(parsed.positions)} sanitized positions "
        f"for statement date {parsed.statement_date.isoformat()}."
    )
    print(
        "Discarded by policy: account identifiers, personal data, cash, "
        "activity, totals, gains/losses, and stock-plan records."
    )
    for position in diff.added:
        print(
            f"+ {position.symbol}: quantity={position.quantity:g} "
            f"average_cost=${position.average_cost:,.2f}"
        )
    for position in diff.removed:
        print(f"- {position.symbol}: removed")
    for old, new in diff.changed:
        print(
            f"~ {new.symbol}: quantity {old.quantity:g}->{new.quantity:g}; "
            f"average_cost ${old.average_cost:,.2f}->${new.average_cost:,.2f}"
        )
    if not diff.added and not diff.removed and not diff.changed:
        print("No position changes detected.")
    print(f"Apply with: portfolio-apply --import-id {import_id}")
    return import_id


def apply_portfolio_import(settings: Settings, import_id: int) -> None:
    database = StockDatabase(settings.db_path)
    database.initialize()
    database.apply_portfolio_preview(import_id)
    positions = database.get_portfolio_positions(import_id)
    print(f"Activated portfolio snapshot {import_id} with {len(positions)} positions.")


def print_portfolio(settings: Settings) -> None:
    database = StockDatabase(settings.db_path)
    database.initialize()
    active_id = database.get_active_portfolio_import_id()
    positions = database.get_portfolio_positions(active_id)
    if active_id is None:
        print("No active portfolio.")
        return
    print(f"Active portfolio snapshot: {active_id} | Positions: {len(positions)}")
    for position in positions:
        print(
            f"{position.symbol}: quantity={position.quantity:g} "
            f"average_cost=${position.average_cost:,.2f} "
            f"classification={position.classification}"
        )


def update_portfolio_policy(
    settings: Settings,
    symbol: str,
    classification: str | None,
    concentration_exempt: bool | None,
    buy_more_enabled: bool | None,
) -> None:
    database = StockDatabase(settings.db_path)
    database.initialize()
    clean_symbol = _required_symbol(symbol, "portfolio-policy")
    existing = database.get_portfolio_policy(clean_symbol)
    policy = database.set_portfolio_policy(
        clean_symbol,
        classification
        if classification is not None
        else existing.classification_override,
        concentration_exempt
        if concentration_exempt is not None
        else existing.concentration_exempt,
        buy_more_enabled
        if buy_more_enabled is not None
        else existing.buy_more_enabled,
    )
    print(
        f"{policy.symbol}: classification="
        f"{policy.classification_override or 'adaptive'} "
        f"concentration_exempt={policy.concentration_exempt} "
        f"buy_more_enabled={policy.buy_more_enabled}"
    )


def print_portfolio_status(settings: Settings) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    status = database.get_portfolio_status()
    print(
        f"Portfolio snapshot: {status['active_import_id'] or 'none'} | "
        f"Positions: {status['position_count']}"
    )
    if status["latest_run"]:
        latest = status["latest_run"]
        print(
            f"Latest run {latest['id']} at {latest['started_at']} | "
            f"coverage={latest['market_coverage_pct']:.1f}% | "
            f"degraded={bool(latest['market_degraded'])} | "
            f"value=${latest['total_invested_value']:,.2f}"
        )
        if status["actions"]:
            print(
                "Actions: "
                + ", ".join(
                    f"{action}={count}"
                    for action, count in sorted(status["actions"].items())
                )
            )
    return status


def print_portfolio_stability(
    settings: Settings,
    runs: int = 10,
    min_gap_hours: float = 2.0,
) -> dict[str, object]:
    database = StockDatabase(settings.db_path)
    database.initialize()
    import_id = database.get_active_portfolio_import_id()
    if import_id is None:
        raise ValueError("No active portfolio.")
    rows = database.get_portfolio_action_history(import_id, limit=max(runs * 3, 20))
    by_run: dict[int, dict[str, object]] = {}
    for row in rows:
        run = by_run.setdefault(
            int(row["run_id"]),
            {
                "started_at": datetime.fromisoformat(row["started_at"]),
                "coverage": float(row["market_coverage_pct"]),
                "degraded": bool(row["market_degraded"]),
                "actions": {},
            },
        )
        run["actions"][row["symbol"]] = row["action"]

    eligible: list[tuple[int, dict[str, object]]] = []
    newest_accepted: datetime | None = None
    for run_id, run in sorted(by_run.items(), reverse=True):
        started_at = run["started_at"]
        if run["degraded"] or run["coverage"] < settings.min_market_coverage_pct:
            continue
        if (
            newest_accepted is not None
            and (newest_accepted - started_at).total_seconds()
            < min_gap_hours * 3600
        ):
            continue
        eligible.append((run_id, run))
        newest_accepted = started_at
        if len(eligible) >= runs:
            break
    eligible.reverse()

    symbols = sorted(
        {
            symbol
            for _, run in eligible
            for symbol in run["actions"]
        }
    )
    stability_rows: list[dict[str, object]] = []
    for symbol in symbols:
        actions = [
            run["actions"][symbol]
            for _, run in eligible
            if symbol in run["actions"]
        ]
        transitions = sum(
            previous != current
            for previous, current in zip(actions, actions[1:])
        )
        current_streak = 0
        if actions:
            current_action = actions[-1]
            for action in reversed(actions):
                if action != current_action:
                    break
                current_streak += 1
        stability_rows.append(
            {
                "symbol": symbol,
                "current_action": actions[-1] if actions else "unknown",
                "observations": len(actions),
                "transitions": transitions,
                "current_streak": current_streak,
            }
        )

    span_hours = 0.0
    if len(eligible) >= 2:
        span_hours = (
            eligible[-1][1]["started_at"] - eligible[0][1]["started_at"]
        ).total_seconds() / 3600
    ready = len(eligible) >= 5 and span_hours >= 10
    unstable = [
        row
        for row in stability_rows
        if row["transitions"] >= 2
    ]
    result = {
        "eligible_runs": len(eligible),
        "span_hours": span_hours,
        "ready_for_hysteresis_decision": ready,
        "unstable": unstable,
        "symbols": stability_rows,
    }
    print(
        f"Portfolio stability: {len(eligible)} spaced healthy runs "
        f"across {span_hours:.1f} hours."
    )
    print(
        "Hysteresis decision: "
        + ("evidence window ready" if ready else "collecting evidence")
    )
    if unstable:
        print(
            "Potential oscillation: "
            + ", ".join(
                f"{row['symbol']} ({row['transitions']} transitions)"
                for row in unstable
            )
        )
    else:
        print("Potential oscillation: none observed.")
    for row in stability_rows:
        if row["current_action"] != "HOLD" or row["transitions"]:
            print(
                f"{row['symbol']}: {row['current_action']} | "
                f"streak={row['current_streak']}/{row['observations']} | "
                f"transitions={row['transitions']}"
            )
    return result


def run_portfolio(settings: Settings) -> str:
    run_at = datetime.now(ZoneInfo(settings.timezone))
    telegram_sender = build_telegram_sender(settings)
    telegram_sender.validate_live_config()
    database = StockDatabase(settings.db_path)
    database.initialize()
    import_id = database.get_active_portfolio_import_id()
    if import_id is None:
        raise ValueError("No active portfolio. Apply a sanitized preview first.")
    positions = database.get_portfolio_positions(import_id)
    position_map = {position.symbol: position for position in positions}
    previous_actions = database.get_latest_portfolio_actions(import_id)
    symbols = list(position_map)
    provider = build_provider(settings)
    histories = provider.get_history(
        symbols=_with_market_context_symbols(symbols),
        period=settings.history_period,
        interval=settings.history_interval,
    )
    market_received = sum(symbol in histories for symbol in symbols)
    coverage = market_received / len(symbols) * 100 if symbols else 0.0
    degraded = (
        coverage < settings.min_market_coverage_pct
        or BENCHMARK_SYMBOL not in histories
        or not symbols
    )
    market_scores = rank_symbols(
        histories=histories,
        budget=settings.alert_budget,
        alert_threshold=settings.alert_score_threshold,
        benchmark_symbol=BENCHMARK_SYMBOL,
        as_of=run_at,
        excluded_symbols=set(MARKET_CONTEXT_SYMBOLS),
    )
    scores_by_symbol = {score.symbol: score for score in market_scores}
    provider_call_ids: list[int] = []
    sec_signals = {}
    shadow_signals = {}
    if not degraded:
        database.begin_provider_call_capture()
        try:
            sec_provider = build_catalyst_provider(
                settings.with_overrides(catalyst_provider="sec"),
                state_store=database,
            )
            sec_signals = sec_provider.fetch_signals(symbols, run_at)
            enriched_scores = apply_catalyst_signals(
                market_scores,
                sec_signals,
                settings.alert_score_threshold,
                settings.alert_budget,
            )
            enriched_scores = database.annotate_calibration_context(enriched_scores)
            scores_by_symbol = {score.symbol: score for score in enriched_scores}
            priority = sorted(
                symbols,
                key=lambda symbol: (
                    position_map[symbol].quantity
                    * scores_by_symbol.get(
                        symbol,
                        StockScore(symbol, 0, 0, "skip", 0),
                    ).last_price
                ),
                reverse=True,
            )[:5]
            shadow_provider = build_catalyst_provider(
                settings.with_overrides(catalyst_provider="multi"),
                state_store=database,
            )
            market_setter = getattr(shadow_provider, "set_market_histories", None)
            if callable(market_setter):
                market_setter(histories)
            shadow_signals = shadow_provider.fetch_signals(priority, run_at)
        finally:
            provider_call_ids = database.finish_provider_call_capture()

    current_values: dict[str, float] = {}
    for symbol, position in position_map.items():
        score = scores_by_symbol.get(symbol)
        current_values[symbol] = (
            position.quantity
            * portfolio_market_price(
                score
                if score is not None
                else StockScore(symbol, 0, 0, "skip", 0),
                histories.get(symbol, pd.DataFrame()),
            )
        )
    total_value = sum(current_values.values())
    assessments = []
    for symbol, position in position_map.items():
        score = scores_by_symbol.get(
            symbol,
            StockScore(
                symbol=symbol,
                score=0,
                last_price=0,
                action="skip",
                suggested_amount=0,
                risks=["Usable market history is unavailable."],
            ),
        )
        assessments.append(
            assess_position(
                position=position,
                score=score,
                history=histories.get(symbol, pd.DataFrame()),
                policy=database.get_portfolio_policy(symbol),
                weight_pct=(
                    current_values[symbol] / total_value * 100
                    if total_value > 0
                    else 0.0
                ),
                degraded=degraded,
                shadow_context=shadow_signals.get(symbol),
            )
        )
    portfolio_run_id = database.create_portfolio_monitor_run(
        import_id=import_id,
        started_at=run_at,
        market_coverage_pct=coverage,
        market_degraded=degraded,
        total_invested_value=total_value,
    )
    database.attach_provider_calls_to_portfolio_run(
        portfolio_run_id,
        provider_call_ids,
    )
    database.insert_portfolio_assessments(portfolio_run_id, assessments)
    report = format_portfolio_report(
        run_at=run_at,
        positions=position_map,
        assessments=assessments,
        coverage_pct=coverage,
        degraded=degraded,
        previous_actions=previous_actions,
    )
    caption = portfolio_pdf_caption(assessments, run_at)
    try:
        pdf_bytes = build_portfolio_alert_pdf(
            run_at=run_at,
            positions=position_map,
            assessments=assessments,
            coverage_pct=coverage,
            degraded=degraded,
            previous_actions=previous_actions,
        )
    except Exception as exc:
        fallback = (
            f"{caption}\nPDF generation failed ({type(exc).__name__}). "
            "Open the local dashboard for the complete report."
        )
        try:
            telegram_sender.send(
                fallback,
                message_kind="portfolio_pdf_fallback",
            )
        except TelegramSendError as send_exc:
            database.update_portfolio_notification_status(
                portfolio_run_id,
                "failed",
                message=str(send_exc),
                notification_format="text_fallback",
            )
            print(
                "Portfolio analysis completed and was stored, but Telegram "
                f"fallback delivery failed: {send_exc}"
            )
        else:
            database.update_portfolio_notification_status(
                portfolio_run_id,
                "dry_run" if settings.dry_run else "delivered",
                message=f"PDF generation failed: {type(exc).__name__}",
                notification_format="text_fallback",
            )
    else:
        try:
            telegram_sender.send_document(
                pdf_bytes,
                portfolio_pdf_filename(run_at),
                caption,
                "portfolio_pdf",
            )
        except TelegramSendError as exc:
            database.update_portfolio_notification_status(
                portfolio_run_id,
                "failed",
                message=str(exc),
                notification_format="pdf",
            )
            print(
                "Portfolio analysis completed and was stored, but Telegram "
                f"PDF delivery failed: {exc}"
            )
        else:
            database.update_portfolio_notification_status(
                portfolio_run_id,
                "dry_run" if settings.dry_run else "delivered",
                notification_format="pdf",
            )
    return report


def run_portfolio_price_watch(settings: Settings, *, force: bool = False) -> list[str]:
    run_at = datetime.now(ZoneInfo(settings.timezone))
    if not force and not is_market_hours(run_at, settings.timezone):
        print("Portfolio price watch skipped: outside regular market hours.")
        return []

    telegram_sender = build_telegram_sender(settings)
    telegram_sender.validate_live_config()
    database = StockDatabase(settings.db_path)
    database.initialize()
    import_id = database.get_active_portfolio_import_id()
    if import_id is None:
        raise ValueError("No active portfolio. Apply a sanitized preview first.")
    positions = {
        position.symbol: position
        for position in database.get_portfolio_positions(import_id)
        if position.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
    }
    if not positions:
        print("Portfolio price watch skipped: no permitted positions.")
        return []

    symbols = list(positions)
    provider = build_provider(settings)
    intraday_histories = provider.get_history(symbols, period="1d", interval="5m")
    daily_histories = provider.get_history(symbols, period="5d", interval="1d")
    trade_date = trade_date_for(run_at, settings.timezone)
    previous_snapshots = database.get_latest_portfolio_price_snapshots(trade_date)
    snapshots = build_price_snapshots(
        positions,
        intraday_histories,
        daily_histories,
        run_at,
        source=provider.name,
        previous_snapshots=previous_snapshots,
    )
    database.insert_portfolio_price_snapshots(snapshots)
    sent_levels = database.get_sent_portfolio_price_alert_levels(trade_date)
    alerts = detect_price_alerts(snapshots, sent_levels, run_at)
    messages: list[str] = []
    for _symbol, symbol_alerts, message in group_alert_messages(snapshots, alerts, run_at):
        try:
            telegram_sender.send(message, message_kind="portfolio_price_swing")
        except TelegramSendError as exc:
            for alert in symbol_alerts:
                database.insert_portfolio_price_alert(
                    alert,
                    "failed",
                    message=str(exc),
                )
            print(f"Portfolio price alert delivery failed: {exc}")
        else:
            status = "dry_run" if settings.dry_run else "delivered"
            for alert in symbol_alerts:
                database.insert_portfolio_price_alert(alert, status)
            messages.append(message)
    if not messages:
        valid = [snapshot for snapshot in snapshots if not snapshot.degraded]
        degraded = [snapshot for snapshot in snapshots if snapshot.degraded]
        print(
            "Portfolio price watch completed: "
            f"{len(valid)}/{len(snapshots)} prices usable, "
            f"{len(degraded)} degraded, {len(alerts)} new alert(s)."
        )
    return messages


def run_portfolio_eod_report(settings: Settings, *, force: bool = False) -> str:
    run_at = datetime.now(ZoneInfo(settings.timezone))
    if not force and not is_eod_report_window(run_at, settings.timezone):
        print("Portfolio EOD report skipped: market close window has not started.")
        return ""

    telegram_sender = build_telegram_sender(settings)
    telegram_sender.validate_live_config()
    database = StockDatabase(settings.db_path)
    database.initialize()
    import_id = database.get_active_portfolio_import_id()
    if import_id is None:
        raise ValueError("No active portfolio. Apply a sanitized preview first.")
    positions = {
        position.symbol: position
        for position in database.get_portfolio_positions(import_id)
        if position.symbol not in EXCLUDED_ANALYSIS_SYMBOLS
    }
    if not positions:
        raise ValueError("No permitted portfolio positions are available.")

    symbols = list(positions)
    provider = build_provider(settings)
    intraday_histories = provider.get_history(symbols, period="1d", interval="5m")
    daily_histories = provider.get_history(symbols, period="5d", interval="1d")
    trade_date = trade_date_for(run_at, settings.timezone)
    previous_snapshots = database.get_latest_portfolio_price_snapshots(trade_date)
    snapshots = build_price_snapshots(
        positions,
        intraday_histories,
        daily_histories,
        run_at,
        source=provider.name,
        previous_snapshots=previous_snapshots,
    )
    database.insert_portfolio_price_snapshots(snapshots)
    report = build_eod_report(snapshots, run_at, source=provider.name)
    report_id = database.create_portfolio_eod_report(report)
    caption = eod_pdf_caption(report)
    try:
        pdf_bytes = build_portfolio_eod_pdf(report)
    except Exception as exc:
        fallback = (
            f"{caption}\nEOD PDF generation failed ({type(exc).__name__}). "
            "Open the local dashboard for portfolio details."
        )
        try:
            telegram_sender.send(fallback, message_kind="portfolio_eod_pdf_fallback")
        except TelegramSendError as send_exc:
            database.update_portfolio_eod_notification_status(
                report_id,
                "failed",
                message=str(send_exc),
                notification_format="text_fallback",
            )
            print(
                "Portfolio EOD report was stored, but Telegram fallback "
                f"delivery failed: {send_exc}"
            )
        else:
            database.update_portfolio_eod_notification_status(
                report_id,
                "dry_run" if settings.dry_run else "delivered",
                message=f"PDF generation failed: {type(exc).__name__}",
                notification_format="text_fallback",
            )
    else:
        try:
            telegram_sender.send_document(
                pdf_bytes,
                eod_pdf_filename(run_at),
                caption,
                "portfolio_eod_pdf",
            )
        except TelegramSendError as exc:
            database.update_portfolio_eod_notification_status(
                report_id,
                "failed",
                message=str(exc),
                notification_format="pdf",
            )
            print(
                "Portfolio EOD report was stored, but Telegram PDF delivery "
                f"failed: {exc}"
            )
        else:
            database.update_portfolio_eod_notification_status(
                report_id,
                "dry_run" if settings.dry_run else "delivered",
                notification_format="pdf",
            )
    return caption


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Moonshot stock analyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once_parser = subparsers.add_parser("run-once", help="Run one scan")
    _add_common_options(run_once_parser)

    schedule_parser = subparsers.add_parser("schedule", help="Run forever on the configured interval")
    _add_common_options(schedule_parser)

    telegram_test_parser = subparsers.add_parser(
        "telegram-test",
        help="Send one Telegram configuration test message",
    )
    _add_common_options(telegram_test_parser)

    telegram_chat_id_parser = subparsers.add_parser(
        "telegram-chat-id",
        help="Print recent Telegram chat IDs from bot updates",
    )
    telegram_chat_id_parser.add_argument("--timeout", type=float, help="Telegram request timeout seconds")

    fmp_test_parser = subparsers.add_parser(
        "fmp-test",
        help="Verify configured FMP API access without sending Telegram messages",
    )
    fmp_test_parser.add_argument("--symbol", default="NVDA", help="Symbol to use for endpoint checks")
    fmp_test_parser.add_argument("--timeout", type=float, help="FMP request timeout seconds")

    finnhub_test_parser = subparsers.add_parser(
        "finnhub-test",
        help="Verify configured Finnhub API access without sending Telegram messages",
    )
    finnhub_test_parser.add_argument("--symbol", default="NVDA", help="Symbol to use for endpoint checks")
    finnhub_test_parser.add_argument("--timeout", type=float, help="Finnhub request timeout seconds")

    marketaux_test_parser = subparsers.add_parser(
        "marketaux-test",
        help="Verify configured Marketaux access without sending Telegram messages",
    )
    marketaux_test_parser.add_argument("--symbol", default="NVDA")
    marketaux_test_parser.add_argument("--timeout", type=float)

    alpha_test_parser = subparsers.add_parser(
        "alpha-vantage-test",
        help="Verify configured Alpha Vantage access without sending Telegram messages",
    )
    alpha_test_parser.add_argument("--symbol", default="NVDA")
    alpha_test_parser.add_argument("--timeout", type=float)

    fred_test_parser = subparsers.add_parser(
        "fred-test",
        help="Verify configured FRED market-context access",
    )
    fred_test_parser.add_argument("--timeout", type=float)

    shadow_status_parser = subparsers.add_parser(
        "shadow-status",
        help="Report multi-source shadow evaluation metrics",
    )
    shadow_status_parser.add_argument("--days", type=int, default=7)
    shadow_status_parser.add_argument("--db-path")

    market_health_parser = subparsers.add_parser(
        "market-health",
        help="Report recent market-data coverage and degraded scans",
    )
    market_health_parser.add_argument("--days", type=int, default=7)
    market_health_parser.add_argument("--db-path")

    outcome_status_parser = subparsers.add_parser(
        "outcome-status",
        help="Report matured forward returns by horizon and action",
    )
    outcome_status_parser.add_argument("--db-path")

    calibration_status_parser = subparsers.add_parser(
        "calibration-status",
        help="Report episode-adjusted returns by action and score band",
    )
    calibration_status_parser.add_argument("--episode-gap-hours", type=float, default=36.0)
    calibration_status_parser.add_argument("--db-path")

    shadow_review_parser = subparsers.add_parser(
        "shadow-review",
        help="Record review of a shadow candidate-state change",
    )
    shadow_review_parser.add_argument("--run-id", type=int, required=True)
    shadow_review_parser.add_argument("--symbol", required=True)
    shadow_review_parser.add_argument(
        "--decision",
        choices=["approved", "rejected", "needs_followup"],
        required=True,
    )
    shadow_review_parser.add_argument("--notes", default="")
    shadow_review_parser.add_argument("--db-path")

    portfolio_import_parser = subparsers.add_parser(
        "portfolio-import",
        help="Create a sanitized portfolio preview from a supported PDF or CSV",
    )
    portfolio_import_source = portfolio_import_parser.add_mutually_exclusive_group(
        required=True
    )
    portfolio_import_source.add_argument("--pdf")
    portfolio_import_source.add_argument("--csv")
    portfolio_import_parser.add_argument("--db-path")

    portfolio_apply_parser = subparsers.add_parser(
        "portfolio-apply",
        help="Atomically activate a sanitized portfolio preview",
    )
    portfolio_apply_parser.add_argument("--import-id", type=int, required=True)
    portfolio_apply_parser.add_argument("--db-path")

    portfolio_show_parser = subparsers.add_parser(
        "portfolio-show",
        help="Show the active sanitized portfolio",
    )
    portfolio_show_parser.add_argument("--db-path")

    portfolio_policy_parser = subparsers.add_parser(
        "portfolio-policy",
        help="Set or inspect per-symbol portfolio policy",
    )
    portfolio_policy_parser.add_argument("--symbol", required=True)
    portfolio_policy_parser.add_argument(
        "--classification",
        choices=[
            "adaptive",
            "core_etf",
            "thematic_etf",
            "established",
            "growth_cyclical",
            "speculative",
        ],
    )
    portfolio_policy_parser.add_argument(
        "--concentration-exempt",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    portfolio_policy_parser.add_argument(
        "--buy-more-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    portfolio_policy_parser.add_argument("--db-path")

    portfolio_run_parser = subparsers.add_parser(
        "portfolio-run",
        help="Run one portfolio monitoring pass",
    )
    portfolio_mode = portfolio_run_parser.add_mutually_exclusive_group()
    portfolio_mode.add_argument("--dry-run", action="store_true")
    portfolio_mode.add_argument("--live", action="store_true")
    portfolio_run_parser.add_argument("--db-path")

    portfolio_price_watch_parser = subparsers.add_parser(
        "portfolio-price-watch",
        help="Check active portfolio positions for 5/10/15 percent intraday moves",
    )
    price_watch_mode = portfolio_price_watch_parser.add_mutually_exclusive_group()
    price_watch_mode.add_argument("--dry-run", action="store_true")
    price_watch_mode.add_argument("--live", action="store_true")
    portfolio_price_watch_parser.add_argument("--db-path")
    portfolio_price_watch_parser.add_argument(
        "--force",
        action="store_true",
        help="Run even outside the regular market-hours guard",
    )

    portfolio_eod_parser = subparsers.add_parser(
        "portfolio-eod-report",
        help="Send the end-of-day portfolio PDF after market close",
    )
    portfolio_eod_mode = portfolio_eod_parser.add_mutually_exclusive_group()
    portfolio_eod_mode.add_argument("--dry-run", action="store_true")
    portfolio_eod_mode.add_argument("--live", action="store_true")
    portfolio_eod_parser.add_argument("--db-path")
    portfolio_eod_parser.add_argument(
        "--force",
        action="store_true",
        help="Run even before the market-close guard",
    )

    portfolio_status_parser = subparsers.add_parser(
        "portfolio-status",
        help="Report active portfolio and latest monitoring status",
    )
    portfolio_status_parser.add_argument("--db-path")

    portfolio_stability_parser = subparsers.add_parser(
        "portfolio-stability",
        help="Measure action stability across spaced healthy portfolio runs",
    )
    portfolio_stability_parser.add_argument("--runs", type=int, default=10)
    portfolio_stability_parser.add_argument(
        "--min-gap-hours",
        type=float,
        default=2.0,
    )
    portfolio_stability_parser.add_argument("--db-path")

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Serve the private read-only decision cockpit on localhost",
    )
    dashboard_parser.add_argument("--port", type=int, default=8765)
    dashboard_parser.add_argument("--db-path")

    init_parser = subparsers.add_parser("init-db", help="Initialize SQLite schema")
    _add_common_options(init_parser)

    return parser.parse_args()


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print Telegram message instead of sending")
    mode.add_argument("--live", action="store_true", help="Send Telegram message using configured credentials")
    parser.add_argument("--max-symbols", type=int, help="Limit universe size for quick tests")
    parser.add_argument("--symbols", help="Comma-separated symbols to scan instead of the configured universe")
    parser.add_argument("--top-n", type=int, help="Number of ranked names to include")
    parser.add_argument("--threshold", type=float, help="Alert score threshold")
    parser.add_argument("--budget", type=float, help="Candidate alert budget")
    parser.add_argument("--db-path", help="SQLite database path")
    parser.add_argument("--no-catalysts", action="store_true", help="Disable catalyst enrichment")
    parser.add_argument(
        "--catalyst-provider",
        choices=[
            "sec",
            "fmp",
            "finnhub",
            "marketaux",
            "alpha_vantage",
            "fred",
            "multi",
            "none",
        ],
        help="Catalyst enrichment provider for this run",
    )
    parser.add_argument("--catalyst-top-n", type=int, help="Number of top market-ranked names to enrich")


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    overrides: dict[str, object] = {}

    if getattr(args, "dry_run", False):
        overrides["dry_run"] = True
    if getattr(args, "live", False):
        overrides["dry_run"] = False
    if getattr(args, "max_symbols", None) is not None:
        overrides["max_symbols"] = args.max_symbols
    if getattr(args, "symbols", None) is not None:
        overrides["manual_symbols"] = _parse_symbols_arg(args.symbols)
    if getattr(args, "top_n", None) is not None:
        overrides["top_n"] = args.top_n
    if getattr(args, "threshold", None) is not None:
        overrides["alert_score_threshold"] = args.threshold
    if getattr(args, "budget", None) is not None:
        overrides["alert_budget"] = clamp_alert_budget(args.budget)
    if getattr(args, "db_path", None) is not None:
        from pathlib import Path

        overrides["db_path"] = Path(args.db_path)
    if getattr(args, "no_catalysts", False):
        overrides["catalyst_provider"] = "none"
    if getattr(args, "catalyst_provider", None) is not None:
        overrides["catalyst_provider"] = args.catalyst_provider
    if getattr(args, "catalyst_top_n", None) is not None:
        overrides["catalyst_top_n"] = args.catalyst_top_n
    if getattr(args, "timeout", None) is not None:
        overrides["request_timeout_seconds"] = args.timeout

    return settings.with_overrides(**overrides)


def _with_benchmark(symbols: list[str]) -> list[str]:
    if BENCHMARK_SYMBOL in symbols:
        return symbols
    return [*symbols, BENCHMARK_SYMBOL]


def validate_catalyst_delivery_mode(settings: Settings) -> None:
    if (
        settings.catalyst_provider in SHADOW_ONLY_CATALYST_PROVIDERS
        and not settings.dry_run
    ):
        raise ValueError(
            f"{settings.catalyst_provider} is shadow-only; rerun with --dry-run."
        )


def _with_market_context_symbols(symbols: list[str]) -> list[str]:
    result = list(symbols)
    for symbol in MARKET_CONTEXT_SYMBOLS:
        if symbol not in result:
            result.append(symbol)
    return result


def _parse_symbols_arg(raw_symbols: str) -> list[str]:
    return _dedupe_symbols(raw_symbols.split(","))


def _suppress_candidate_alerts(
    scores: list[StockScore],
    alert_threshold: float,
) -> list[StockScore]:
    return [
        replace(
            score,
            action="watch" if score.score >= alert_threshold - 10 else "skip",
            suggested_amount=0.0,
            risks=[
                "Candidate alerts suppressed because market-data coverage was degraded.",
                *score.risks,
            ],
        )
        for score in scores
    ]


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        clean = symbol.strip().upper()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped


def main() -> None:
    args = parse_args()
    settings = settings_from_args(args)

    try:
        if args.command == "run-once":
            run_once(settings)
        elif args.command == "schedule":
            schedule(settings)
        elif args.command == "telegram-test":
            send_telegram_test(settings)
        elif args.command == "telegram-chat-id":
            print_telegram_chat_ids(settings)
        elif args.command == "fmp-test":
            run_fmp_test(settings, symbol=args.symbol)
        elif args.command == "finnhub-test":
            run_finnhub_test(settings, symbol=args.symbol)
        elif args.command == "marketaux-test":
            run_marketaux_test(settings, symbol=args.symbol)
        elif args.command == "alpha-vantage-test":
            run_alpha_vantage_test(settings, symbol=args.symbol)
        elif args.command == "fred-test":
            run_fred_test(settings)
        elif args.command == "shadow-status":
            print_shadow_status(settings, days=args.days)
        elif args.command == "market-health":
            print_market_health(settings, days=args.days)
        elif args.command == "outcome-status":
            print_outcome_status(settings)
        elif args.command == "calibration-status":
            print_calibration_status(settings, episode_gap_hours=args.episode_gap_hours)
        elif args.command == "shadow-review":
            record_shadow_review(
                settings,
                run_id=args.run_id,
                symbol=args.symbol,
                decision=args.decision,
                notes=args.notes,
            )
        elif args.command == "portfolio-import":
            preview_portfolio_import(settings, pdf_path=args.pdf, csv_path=args.csv)
        elif args.command == "portfolio-apply":
            apply_portfolio_import(settings, args.import_id)
        elif args.command == "portfolio-show":
            print_portfolio(settings)
        elif args.command == "portfolio-policy":
            update_portfolio_policy(
                settings,
                symbol=args.symbol,
                classification=args.classification,
                concentration_exempt=args.concentration_exempt,
                buy_more_enabled=args.buy_more_enabled,
            )
        elif args.command == "portfolio-run":
            run_portfolio(settings)
        elif args.command == "portfolio-price-watch":
            run_portfolio_price_watch(settings, force=args.force)
        elif args.command == "portfolio-eod-report":
            run_portfolio_eod_report(settings, force=args.force)
        elif args.command == "portfolio-status":
            print_portfolio_status(settings)
        elif args.command == "portfolio-stability":
            print_portfolio_stability(
                settings,
                runs=max(1, args.runs),
                min_gap_hours=max(0.0, args.min_gap_hours),
            )
        elif args.command == "dashboard":
            run_dashboard(settings.db_path, port=args.port)
        elif args.command == "init-db":
            initialize_database(settings)
        else:
            raise ValueError(f"Unknown command: {args.command}")
    except TelegramConfigError as exc:
        raise SystemExit(f"Telegram configuration error: {exc}") from None
    except TelegramSendError as exc:
        raise SystemExit(f"Telegram delivery error: {exc}") from None
    except PortfolioImportError as exc:
        raise SystemExit(str(exc)) from None


def _required_symbol(symbol: str, command: str) -> str:
    symbols = _dedupe_symbols([symbol])
    if not symbols:
        raise SystemExit(f"A non-empty --symbol is required for {command}.")
    return symbols[0]


def _print_checks(
    provider: str,
    subject: str,
    checks: list[object],
    calls: int,
) -> None:
    print(f"{provider} smoke test for {subject}")
    print(f"Calls used: {calls}")
    for check in checks:
        status = "OK" if getattr(check, "ok") else "FAIL"
        print(
            f"- {getattr(check, 'name')}: {status} "
            f"({getattr(check, 'item_count')} item(s)) {getattr(check, 'message')}"
        )


if __name__ == "__main__":
    main()
    FredEndpointCheck,
    FredMarketContextProvider,
    MarketauxCatalystProvider,
    MarketauxEndpointCheck,
