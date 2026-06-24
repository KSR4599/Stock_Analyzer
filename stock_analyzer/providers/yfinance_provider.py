from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from stock_analyzer.providers.base import DataProvider


@dataclass(frozen=True)
class MarketDataHealth:
    requested_symbols: int
    returned_symbols: int
    failed_symbols: tuple[str, ...]
    retry_requests: int

    @property
    def coverage_pct(self) -> float:
        if self.requested_symbols == 0:
            return 100.0
        return self.returned_symbols / self.requested_symbols * 100


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def __init__(
        self,
        max_symbols_per_batch: int = 120,
        retry_batch_size: int = 20,
        max_single_symbol_retries: int = 10,
    ) -> None:
        self.max_symbols_per_batch = max_symbols_per_batch
        self.retry_batch_size = retry_batch_size
        self.max_single_symbol_retries = max_single_symbol_retries
        self.last_health = MarketDataHealth(0, 0, (), 0)

    def get_history(
        self,
        symbols: list[str],
        period: str,
        interval: str,
    ) -> dict[str, pd.DataFrame]:
        histories: dict[str, pd.DataFrame] = {}
        canonical_symbols = _dedupe(symbols)
        retry_requests = 0

        for batch in _chunks(canonical_symbols, self.max_symbols_per_batch):
            histories.update(self._download_batch(batch, period, interval))

        missing = [symbol for symbol in canonical_symbols if symbol not in histories]
        for batch in _chunks(missing, self.retry_batch_size):
            retry_requests += 1
            histories.update(self._download_batch(batch, period, interval))

        missing = [symbol for symbol in canonical_symbols if symbol not in histories]
        for symbol in missing[: self.max_single_symbol_retries]:
            retry_requests += 1
            histories.update(self._download_batch([symbol], period, interval))

        failed = tuple(symbol for symbol in canonical_symbols if symbol not in histories)
        self.last_health = MarketDataHealth(
            requested_symbols=len(canonical_symbols),
            returned_symbols=len(histories),
            failed_symbols=failed,
            retry_requests=retry_requests,
        )

        return histories

    def _download_batch(
        self,
        batch: list[str],
        period: str,
        interval: str,
    ) -> dict[str, pd.DataFrame]:
        if not batch:
            return {}
        yahoo_symbols = [_to_yahoo_symbol(symbol) for symbol in batch]
        try:
            raw = yf.download(
                tickers=yahoo_symbols,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                threads=len(batch) > 1,
                progress=False,
            )
        except Exception:
            return {}

        histories: dict[str, pd.DataFrame] = {}
        for canonical, yahoo_symbol in zip(batch, yahoo_symbols, strict=True):
            frame = _extract_symbol_frame(raw, yahoo_symbol, len(batch) == 1)
            if frame is None or frame.empty:
                continue
            cleaned = _clean_frame(frame)
            if not cleaned.empty:
                histories[canonical] = cleaned
        return histories


def _to_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").upper()


def _dedupe(symbols: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        clean = symbol.strip().upper()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("Chunk size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _extract_symbol_frame(
    raw: pd.DataFrame,
    yahoo_symbol: str,
    single_symbol: bool,
) -> pd.DataFrame | None:
    if raw.empty:
        return None

    if not isinstance(raw.columns, pd.MultiIndex):
        return raw if single_symbol else None

    first_level = raw.columns.get_level_values(0)
    second_level = raw.columns.get_level_values(1)

    if yahoo_symbol in first_level:
        return raw[yahoo_symbol]

    if yahoo_symbol in second_level:
        return raw.xs(yahoo_symbol, axis=1, level=1)

    return None


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned.columns = [str(column).strip().lower() for column in cleaned.columns]
    needed = {"close", "volume"}
    if not needed.issubset(set(cleaned.columns)):
        return pd.DataFrame()

    cleaned = cleaned.dropna(subset=["close"])
    cleaned = cleaned[cleaned["close"] > 0]
    cleaned["volume"] = pd.to_numeric(cleaned["volume"], errors="coerce").fillna(0)
    cleaned = cleaned[cleaned["volume"] >= 0]
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
    cleaned = cleaned.sort_index()
    return cleaned
