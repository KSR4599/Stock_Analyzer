from __future__ import annotations

from argparse import Namespace

from stock_analyzer.app import _parse_symbols_arg, _with_benchmark, settings_from_args


def test_parse_symbols_arg_normalizes_and_dedupes() -> None:
    assert _parse_symbols_arg(" arm, MRVL,arm, , souN ") == ["ARM", "MRVL", "SOUN"]


def test_with_benchmark_adds_spy_once() -> None:
    assert _with_benchmark(["ARM", "SPY"]) == ["ARM", "SPY"]
    assert _with_benchmark(["ARM"]) == ["ARM", "SPY"]


def test_settings_from_args_accepts_catalyst_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        dry_run=False,
        live=False,
        max_symbols=None,
        symbols=None,
        top_n=None,
        threshold=None,
        budget=None,
        db_path=None,
        no_catalysts=False,
        catalyst_provider="fmp",
        catalyst_top_n=5,
        timeout=None,
    )

    settings = settings_from_args(args)

    assert settings.catalyst_provider == "fmp"
    assert settings.catalyst_top_n == 5
