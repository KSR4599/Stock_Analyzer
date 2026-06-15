from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
import yfinance as yf

from stock_analyzer.providers.base import DataProvider


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def __init__(self, max_symbols_per_batch: int = 120) -> None:
        self.max_symbols_per_batch = max_symbols_per_batch

    def get_history(
        self,
        symbols: list[str],
        period: str,
        interval: str,
    ) -> dict[str, pd.DataFrame]:
        histories: dict[str, pd.DataFrame] = {}
        canonical_symbols = _dedupe(symbols)

        for batch in _chunks(canonical_symbols, self.max_symbols_per_batch):
            yahoo_symbols = [_to_yahoo_symbol(symbol) for symbol in batch]
            raw = yf.download(
                tickers=yahoo_symbols,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )

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
    cleaned = cleaned.sort_index()
    return cleaned
