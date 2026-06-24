from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


MAX_ALERT_BUDGET = 250.0


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _string_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _optional_string_env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def clamp_alert_budget(value: float) -> float:
    return max(0.0, min(value, MAX_ALERT_BUDGET))


DEFAULT_EXTRA_SYMBOLS = [
    "SMCI",
    "ARM",
    "SOUN",
    "MU",
    "SNDK",
    "MRVL",
    "INTC",
    "NVDA",
    "AMD",
    "AVGO",
    "TSM",
    "ASML",
    "PLTR",
    "CRWD",
    "NET",
    "IONQ",
    "RGTI",
    "QBTS",
    "ASTS",
    "RKLB",
]


@dataclass(frozen=True)
class Settings:
    provider: str = "yfinance"
    db_path: Path = Path("data/stock_analyzer.sqlite3")
    dry_run: bool = True
    interval_hours: float = 3.0
    alert_budget: float = 250.0
    alert_score_threshold: float = 78.0
    top_n: int = 10
    send_only_alerts: bool = False
    catalyst_provider: str = "sec"
    catalyst_top_n: int = 12
    catalyst_lookback_hours: int = 72
    catalyst_max_news_articles: int = 6
    finnhub_max_symbols_per_run: int = 5
    fmp_max_symbols_per_run: int = 5
    marketaux_max_symbols_per_run: int = 5
    alpha_vantage_max_symbols_per_run: int = 10
    marketaux_min_match_score: float = 10.0
    alpha_vantage_daily_call_budget: int = 20
    sec_user_agent: str = "stock-analyzer/0.1 personal research contact@example.com"
    sec_lookback_days: int = 14
    sec_max_filings: int = 20
    finnhub_api_key: str | None = None
    fmp_api_key: str | None = None
    marketaux_api_token: str | None = None
    alpha_vantage_api_key: str | None = None
    fred_api_key: str | None = None
    include_sp500: bool = True
    manual_symbols: list[str] | None = None
    extra_symbols: list[str] | None = None
    max_symbols: int | None = None
    history_period: str = "1y"
    history_interval: str = "1d"
    max_symbols_per_batch: int = 120
    min_market_coverage_pct: float = 90.0
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    allowed_telegram_chat_ids: list[str] | None = None
    request_timeout_seconds: float = 20.0
    timezone: str = "America/Los_Angeles"

    def with_overrides(self, **kwargs: object) -> "Settings":
        return replace(self, **kwargs)


def load_settings() -> Settings:
    _load_dotenv()
    telegram_chat_id = _optional_string_env("TELEGRAM_CHAT_ID")
    allowed_telegram_chat_ids = _string_csv_env("ALLOWED_TELEGRAM_CHAT_IDS", [])
    if not allowed_telegram_chat_ids and telegram_chat_id:
        allowed_telegram_chat_ids = [telegram_chat_id]

    return Settings(
        provider=os.getenv("STOCK_ANALYZER_PROVIDER", "yfinance").strip().lower(),
        db_path=Path(os.getenv("STOCK_ANALYZER_DB_PATH", "data/stock_analyzer.sqlite3")),
        dry_run=_bool_env("STOCK_ANALYZER_DRY_RUN", True),
        interval_hours=_float_env("STOCK_ANALYZER_INTERVAL_HOURS", 3.0),
        alert_budget=clamp_alert_budget(
            _float_env("STOCK_ANALYZER_ALERT_BUDGET", MAX_ALERT_BUDGET)
        ),
        alert_score_threshold=_float_env("STOCK_ANALYZER_ALERT_SCORE_THRESHOLD", 78.0),
        top_n=_int_env("STOCK_ANALYZER_TOP_N", 10),
        send_only_alerts=_bool_env("STOCK_ANALYZER_SEND_ONLY_ALERTS", False),
        catalyst_provider=os.getenv("STOCK_ANALYZER_CATALYST_PROVIDER", "sec").strip().lower(),
        catalyst_top_n=_int_env("STOCK_ANALYZER_CATALYST_TOP_N", 12),
        catalyst_lookback_hours=_int_env("STOCK_ANALYZER_CATALYST_LOOKBACK_HOURS", 72),
        catalyst_max_news_articles=_int_env("STOCK_ANALYZER_CATALYST_MAX_NEWS_ARTICLES", 6),
        finnhub_max_symbols_per_run=_int_env("STOCK_ANALYZER_FINNHUB_MAX_SYMBOLS_PER_RUN", 5),
        fmp_max_symbols_per_run=_int_env("STOCK_ANALYZER_FMP_MAX_SYMBOLS_PER_RUN", 5),
        marketaux_max_symbols_per_run=_int_env("STOCK_ANALYZER_MARKETAUX_MAX_SYMBOLS_PER_RUN", 5),
        alpha_vantage_max_symbols_per_run=_int_env(
            "STOCK_ANALYZER_ALPHA_VANTAGE_MAX_SYMBOLS_PER_RUN",
            10,
        ),
        marketaux_min_match_score=_float_env(
            "STOCK_ANALYZER_MARKETAUX_MIN_MATCH_SCORE",
            10.0,
        ),
        alpha_vantage_daily_call_budget=_int_env(
            "STOCK_ANALYZER_ALPHA_VANTAGE_DAILY_CALL_BUDGET",
            20,
        ),
        sec_user_agent=os.getenv(
            "SEC_USER_AGENT",
            "stock-analyzer/0.1 personal research contact@example.com",
        ),
        sec_lookback_days=_int_env("STOCK_ANALYZER_SEC_LOOKBACK_DAYS", 14),
        sec_max_filings=_int_env("STOCK_ANALYZER_SEC_MAX_FILINGS", 20),
        finnhub_api_key=_optional_string_env("FINNHUB_API_KEY"),
        fmp_api_key=_optional_string_env("FMP_API_KEY"),
        marketaux_api_token=_optional_string_env("MARKETAUX_API_TOKEN"),
        alpha_vantage_api_key=_optional_string_env("ALPHA_VANTAGE_API_KEY"),
        fred_api_key=_optional_string_env("FRED_API_KEY"),
        include_sp500=_bool_env("STOCK_ANALYZER_INCLUDE_SP500", True),
        manual_symbols=_csv_env("STOCK_ANALYZER_SYMBOLS", []),
        extra_symbols=_csv_env("STOCK_ANALYZER_EXTRA_SYMBOLS", DEFAULT_EXTRA_SYMBOLS),
        max_symbols=_optional_int_env("STOCK_ANALYZER_MAX_SYMBOLS"),
        history_period=os.getenv("STOCK_ANALYZER_HISTORY_PERIOD", "1y"),
        history_interval=os.getenv("STOCK_ANALYZER_HISTORY_INTERVAL", "1d"),
        max_symbols_per_batch=_int_env("STOCK_ANALYZER_MAX_SYMBOLS_PER_BATCH", 120),
        min_market_coverage_pct=_float_env("STOCK_ANALYZER_MIN_MARKET_COVERAGE_PCT", 90.0),
        telegram_bot_token=_optional_string_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=telegram_chat_id,
        allowed_telegram_chat_ids=allowed_telegram_chat_ids,
        request_timeout_seconds=_float_env("STOCK_ANALYZER_REQUEST_TIMEOUT_SECONDS", 20.0),
        timezone=os.getenv("STOCK_ANALYZER_TIMEZONE", "America/Los_Angeles"),
    )
