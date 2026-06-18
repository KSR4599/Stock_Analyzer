from __future__ import annotations

import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_analyzer.catalysts import (
    FmpCatalystProvider,
    FmpEndpointCheck,
    NullCatalystProvider,
    SecEdgarCatalystProvider,
    apply_catalyst_signals,
    run_fmp_smoke_test,
)
from stock_analyzer.catalysts.base import CatalystProvider
from stock_analyzer.config import Settings, clamp_alert_budget, load_settings
from stock_analyzer.database import StockDatabase
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


def build_provider(settings: Settings) -> DataProvider:
    if settings.provider == "yfinance":
        return YFinanceProvider(max_symbols_per_batch=settings.max_symbols_per_batch)
    raise ValueError(f"Unsupported provider: {settings.provider}")


def build_catalyst_provider(settings: Settings) -> CatalystProvider:
    if settings.catalyst_provider in {"", "none", "off", "disabled"}:
        return NullCatalystProvider("Catalyst enrichment disabled by configuration.")
    if settings.catalyst_provider == "sec":
        return SecEdgarCatalystProvider(
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_days=settings.sec_lookback_days,
            max_filings=settings.sec_max_filings,
        )
    if settings.catalyst_provider == "fmp":
        if not settings.fmp_api_key:
            return NullCatalystProvider("FMP catalyst enrichment disabled because FMP_API_KEY is not set.")
        return FmpCatalystProvider(
            api_key=settings.fmp_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            lookback_hours=settings.catalyst_lookback_hours,
            max_news_articles=settings.catalyst_max_news_articles,
        )
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
    catalyst_provider = build_catalyst_provider(settings)
    database = StockDatabase(settings.db_path)
    database.initialize()

    if settings.manual_symbols:
        universe = _dedupe_symbols(settings.manual_symbols)
        universe_source = "manual_symbols"
    else:
        universe, universe_source = build_universe(
            include_sp500=settings.include_sp500,
            extra_symbols=settings.extra_symbols,
            max_symbols=settings.max_symbols,
            timeout_seconds=settings.request_timeout_seconds,
        )
    fetch_symbols = _with_benchmark(universe)

    histories = provider.get_history(
        symbols=fetch_symbols,
        period=settings.history_period,
        interval=settings.history_interval,
    )
    scores = rank_symbols(
        histories=histories,
        budget=settings.alert_budget,
        alert_threshold=settings.alert_score_threshold,
        benchmark_symbol=BENCHMARK_SYMBOL,
        as_of=run_at,
    )
    if catalyst_provider.name == "none":
        enrichment_symbols = []
        catalyst_signals = {}
    else:
        enrichment_limit = settings.catalyst_top_n
        if catalyst_provider.name == "fmp":
            enrichment_limit = min(enrichment_limit, settings.fmp_max_symbols_per_run)
        enrichment_symbols = [score.symbol for score in scores[:enrichment_limit]]
        catalyst_signals = catalyst_provider.fetch_signals(enrichment_symbols, run_at)
    scores = apply_catalyst_signals(
        scores=scores,
        signals=catalyst_signals,
        alert_threshold=settings.alert_score_threshold,
        budget=settings.alert_budget,
    )

    run_id = database.create_run(
        started_at=run_at,
        provider=provider.name,
        universe_source=universe_source,
        universe_size=len(universe),
    )
    database.insert_scores(run_id=run_id, scores=scores)
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
    )
    telegram_sender.send(report, message_kind=report_kind)
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
        choices=["sec", "fmp", "none"],
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


def _parse_symbols_arg(raw_symbols: str) -> list[str]:
    return _dedupe_symbols(raw_symbols.split(","))


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
        elif args.command == "init-db":
            initialize_database(settings)
        else:
            raise ValueError(f"Unknown command: {args.command}")
    except TelegramConfigError as exc:
        raise SystemExit(f"Telegram configuration error: {exc}") from None
    except TelegramSendError as exc:
        raise SystemExit(f"Telegram delivery error: {exc}") from None


if __name__ == "__main__":
    main()
