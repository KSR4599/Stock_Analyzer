from __future__ import annotations

import logging
import re
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pdfplumber

from stock_analyzer.exclusions import EXCLUDED_ANALYSIS_SYMBOLS
from stock_analyzer.portfolio_models import PortfolioParseResult, PortfolioPosition


logging.getLogger("pdfminer").setLevel(logging.ERROR)


PARSER_VERSION = "fidelity-positions-v2"
CSV_PARSER_VERSION = "fidelity-positions-csv-v1"
EXCLUDED_SYMBOLS = {"FCASH", "RSUS", *EXCLUDED_ANALYSIS_SYMBOLS}
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
STATEMENT_DATE_PATTERN = re.compile(
    r"As of\s+([A-Z][a-z]{2}-\d{1,2}-\d{4})",
    re.IGNORECASE,
)
FILENAME_DATE_PATTERN = re.compile(
    r"([A-Z][a-z]{2}-\d{1,2}-\d{4})",
    re.IGNORECASE,
)
SENSITIVE_SANITIZED_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[A-Z]\d{8,}\b", re.IGNORECASE),
    re.compile(r"\bRSUS?\b", re.IGNORECASE),
    re.compile(r"@"),
    re.compile(r"\b\d{10,}\b"),
]
SECTION_STOP_MARKERS = (
    "pending activity",
    "account total",
    "stock plans",
    "restricted stock",
    "vesting schedule",
)


class PortfolioImportError(RuntimeError):
    """Sanitized portfolio-import error with no source text or file path."""

    def __init__(self, code: str, page: int | None = None, row: int | None = None):
        location = ""
        if page is not None:
            location += f" page={page}"
        if row is not None:
            location += f" row={row}"
        super().__init__(f"Portfolio import failed: {code}{location}")
        self.code = code


def parse_fidelity_positions_pdf(path: Path) -> PortfolioParseResult:
    try:
        with pdfplumber.open(path) as document:
            if not document.pages:
                raise PortfolioImportError("EMPTY_DOCUMENT")
            statement_date = _statement_date(document.pages[0].extract_text() or "")
            parsed: list[PortfolioPosition] = []
            for page_number, page in enumerate(document.pages, start=1):
                text = page.extract_text() or ""
                if page_number > 2 and "Stock Plans" in text:
                    break
                for table in page.extract_tables():
                    if not _looks_like_positions_table(table):
                        continue
                    parsed.extend(_parse_table(table, page_number))
    except PortfolioImportError:
        raise
    except Exception:
        raise PortfolioImportError("PDF_READ_ERROR") from None

    if not parsed:
        raise PortfolioImportError("NO_VALID_POSITIONS")
    positions = _aggregate_positions(parsed)
    _validate_sanitized_positions(positions)
    return PortfolioParseResult(statement_date=statement_date, positions=positions)


def parse_fidelity_positions_csv(path: Path) -> PortfolioParseResult:
    try:
        statement_date = _statement_date_from_filename(path.name)
        parsed: list[PortfolioPosition] = []
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"Symbol", "Quantity", "Average Cost Basis"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise PortfolioImportError("CSV_HEADERS_UNSUPPORTED")
            for row_number, row in enumerate(reader, start=2):
                raw_symbol = str(row.get("Symbol") or "").strip()
                if not raw_symbol:
                    continue
                symbol = _normalize_csv_symbol(raw_symbol)
                row_text = " ".join(str(value or "") for value in row.values())
                if any(marker in row_text.lower() for marker in SECTION_STOP_MARKERS):
                    continue
                if symbol in EXCLUDED_SYMBOLS:
                    continue
                if not SYMBOL_PATTERN.fullmatch(symbol):
                    continue
                try:
                    quantity = _number(row.get("Quantity"))
                    average_cost = _number(row.get("Average Cost Basis"))
                except ValueError:
                    raise PortfolioImportError(
                        "CSV_ROW_VALIDATION_FAILED",
                        row=row_number,
                    ) from None
                if quantity <= 0 or average_cost <= 0:
                    raise PortfolioImportError(
                        "CSV_ROW_VALIDATION_FAILED",
                        row=row_number,
                    )
                parsed.append(
                    PortfolioPosition(
                        symbol=symbol,
                        quantity=round(quantity, 6),
                        average_cost=round(average_cost, 4),
                        classification=_default_import_classification(symbol),
                    )
                )
    except PortfolioImportError:
        raise
    except Exception:
        raise PortfolioImportError("CSV_READ_ERROR") from None

    if not parsed:
        raise PortfolioImportError("NO_VALID_POSITIONS")
    positions = _aggregate_positions(parsed)
    _validate_sanitized_positions(positions)
    return PortfolioParseResult(statement_date=statement_date, positions=positions)


def _statement_date(text: str):
    match = STATEMENT_DATE_PATTERN.search(text)
    if match is None:
        raise PortfolioImportError("STATEMENT_DATE_NOT_FOUND", page=1)
    try:
        return datetime.strptime(match.group(1), "%b-%d-%Y").date()
    except ValueError:
        raise PortfolioImportError("STATEMENT_DATE_INVALID", page=1) from None


def _statement_date_from_filename(filename: str):
    match = FILENAME_DATE_PATTERN.search(filename)
    if match is None:
        raise PortfolioImportError("STATEMENT_DATE_NOT_FOUND")
    try:
        return datetime.strptime(match.group(1), "%b-%d-%Y").date()
    except ValueError:
        raise PortfolioImportError("STATEMENT_DATE_INVALID") from None


def _normalize_csv_symbol(raw_symbol: str) -> str:
    return raw_symbol.strip().upper().rstrip("*")


def _looks_like_positions_table(table: list[list[str | None]]) -> bool:
    if not table:
        return False
    header_text = " ".join(
        str(cell or "").replace("\n", " ") for row in table[:3] for cell in row
    ).lower()
    return "symbol" in header_text and "quantity" in header_text


def _parse_table(
    table: list[list[str | None]],
    page_number: int,
) -> list[PortfolioPosition]:
    positions: list[PortfolioPosition] = []
    for row_number, row in enumerate(table, start=1):
        if not row or not row[0]:
            continue
        row_text = " ".join(str(cell or "").replace("\n", " ") for cell in row)
        if any(marker in row_text.lower() for marker in SECTION_STOP_MARKERS):
            break
        first = str(row[0]).strip().split()
        if not first:
            continue
        raw_symbol = first[0]
        symbol = raw_symbol.upper()
        if raw_symbol != symbol:
            continue
        if symbol in EXCLUDED_SYMBOLS:
            continue
        if not SYMBOL_PATTERN.fullmatch(symbol):
            continue
        if symbol in {"SYMBOL", "ACCOUNT"}:
            continue
        try:
            position = _parse_position_row(symbol, row)
        except (ValueError, IndexError, ZeroDivisionError):
            raise PortfolioImportError(
                "ROW_VALIDATION_FAILED",
                page=page_number,
                row=row_number,
            ) from None
        positions.append(position)
    return positions


def _parse_position_row(
    symbol: str,
    row: list[str | None],
) -> PortfolioPosition:
    if len(row) >= 13:
        last_price = _number(row[1])
        total_gain = _number(row[6])
        current_value = _number(row[8])
        quantity = _number(row[10])
    elif len(row) >= 12:
        last_price = _number(row[1])
        total_gain = _number(row[5])
        current_value = _number(row[7])
        quantity = _number(row[9])
    else:
        raise ValueError("unsupported row")

    if last_price <= 0 or current_value <= 0 or quantity <= 0:
        raise ValueError("non-positive values")
    tolerance = max(1.0, current_value * 0.01)
    if abs(quantity * last_price - current_value) > tolerance:
        raise ValueError("position arithmetic mismatch")

    total_cost = current_value - total_gain
    average_cost = total_cost / quantity
    if average_cost <= 0:
        raise ValueError("invalid average cost")
    return PortfolioPosition(
        symbol=symbol,
        quantity=round(quantity, 6),
        average_cost=round(average_cost, 4),
        classification=_default_import_classification(symbol),
    )


def _number(value: str | None) -> float:
    if value is None:
        raise ValueError("missing numeric value")
    cleaned = (
        str(value)
        .replace("\n", "")
        .replace("$", "")
        .replace(",", "")
        .replace("%", "")
        .replace("+", "")
        .strip()
    )
    if cleaned in {"", "--"}:
        raise ValueError("empty numeric value")
    return float(cleaned)


def _aggregate_positions(
    positions: list[PortfolioPosition],
) -> list[PortfolioPosition]:
    quantities: dict[str, float] = defaultdict(float)
    total_costs: dict[str, float] = defaultdict(float)
    classifications: dict[str, str] = {}
    for position in positions:
        quantities[position.symbol] += position.quantity
        total_costs[position.symbol] += position.quantity * position.average_cost
        classifications[position.symbol] = position.classification
    result = [
        PortfolioPosition(
            symbol=symbol,
            quantity=round(quantity, 6),
            average_cost=round(total_costs[symbol] / quantity, 4),
            classification=classifications[symbol],
        )
        for symbol, quantity in quantities.items()
    ]
    return sorted(result, key=lambda item: item.symbol)


def _validate_sanitized_positions(positions: list[PortfolioPosition]) -> None:
    for position in positions:
        values = [
            position.symbol,
            f"{position.quantity:.6f}",
            f"{position.average_cost:.4f}",
            position.classification,
        ]
        for value in values:
            if any(pattern.search(value) for pattern in SENSITIVE_SANITIZED_PATTERNS):
                raise PortfolioImportError("SANITIZED_DATA_REJECTED")


def _default_import_classification(symbol: str) -> str:
    if symbol in {"VOO", "VTI", "IVV", "SPY"}:
        return "core_etf"
    if symbol in {"DRAM", "QQQ", "SOXX", "IWM"}:
        return "thematic_etf"
    return "adaptive"
