from __future__ import annotations

from bs4 import BeautifulSoup
import requests

from stock_analyzer.config import DEFAULT_EXTRA_SYMBOLS
from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REQUEST_HEADERS = {
    "User-Agent": "stock-analyzer/0.1 personal research bot (contact: local-user)",
}

# Fallback keeps local/dry-run development useful if Wikipedia is unavailable.
SP500_FALLBACK_SYMBOLS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "GOOG",
    "AVGO",
    "TSLA",
    "BRK.B",
    "JPM",
    "LLY",
    "V",
    "MA",
    "NFLX",
    "COST",
    "WMT",
    "AMD",
    "ORCL",
    "CRM",
    "ADBE",
    "NOW",
    "QCOM",
    "TXN",
    "AMAT",
    "LRCX",
    "KLAC",
    "INTC",
    "MU",
    "MRVL",
    "PANW",
    "CRWD",
    "PLTR",
    "GE",
    "BA",
    "CAT",
    "DE",
    "XOM",
    "CVX",
    "UBER",
]


def fetch_sp500_symbols(timeout_seconds: float = 20.0) -> list[str]:
    response = requests.get(SP500_WIKI_URL, headers=REQUEST_HEADERS, timeout=timeout_seconds)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise RuntimeError("No S&P 500 constituents table found")

    header_cells = table.find("tr").find_all(["th", "td"])
    headers = [cell.get_text(strip=True) for cell in header_cells]
    if "Symbol" not in headers:
        raise RuntimeError("S&P 500 table did not include a Symbol column")

    symbol_index = headers.index("Symbol")
    symbols: list[str] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= symbol_index:
            continue
        symbol = cells[symbol_index].get_text(strip=True).upper()
        if symbol:
            symbols.append(symbol)

    if not symbols:
        raise RuntimeError("S&P 500 table did not include any symbols")

    return symbols


def build_universe(
    include_sp500: bool = True,
    extra_symbols: list[str] | None = None,
    max_symbols: int | None = None,
    timeout_seconds: float = 20.0,
) -> tuple[list[str], str]:
    sp500_symbols: list[str] = []
    source = "custom"
    extras = extra_symbols or DEFAULT_EXTRA_SYMBOLS

    if include_sp500:
        try:
            sp500_symbols.extend(fetch_sp500_symbols(timeout_seconds=timeout_seconds))
            source = "sp500_wikipedia"
        except Exception:
            sp500_symbols.extend(SP500_FALLBACK_SYMBOLS)
            source = "sp500_fallback"

    if max_symbols is not None:
        symbols = [*extras, *sp500_symbols]
    else:
        symbols = [*sp500_symbols, *extras]

    deduped = [
        symbol
        for symbol in _dedupe(symbols)
        if symbol not in EXCLUDED_ANALYSIS_SYMBOLS
    ]

    if max_symbols is not None:
        deduped = deduped[:max_symbols]

    return deduped, source


def _dedupe(symbols: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        clean = symbol.strip().upper()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped
