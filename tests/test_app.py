from __future__ import annotations

from stock_analyzer.app import _parse_symbols_arg, _with_benchmark


def test_parse_symbols_arg_normalizes_and_dedupes() -> None:
    assert _parse_symbols_arg(" arm, MRVL,arm, , souN ") == ["ARM", "MRVL", "SOUN"]


def test_with_benchmark_adds_spy_once() -> None:
    assert _with_benchmark(["ARM", "SPY"]) == ["ARM", "SPY"]
    assert _with_benchmark(["ARM"]) == ["ARM", "SPY"]
