from __future__ import annotations

from stock_analyzer import universe


def test_capped_universe_prioritizes_extra_symbols(monkeypatch) -> None:
    monkeypatch.setattr(universe, "fetch_sp500_symbols", lambda timeout_seconds: ["AAPL", "MSFT", "NVDA"])

    symbols, source = universe.build_universe(
        include_sp500=True,
        extra_symbols=["SMCI", "ARM", "NVDA"],
        max_symbols=4,
    )

    assert source == "sp500_wikipedia"
    assert symbols == ["SMCI", "ARM", "NVDA", "AAPL"]


def test_wmt_is_excluded_from_fetched_and_extra_symbols(monkeypatch) -> None:
    monkeypatch.setattr(
        universe,
        "fetch_sp500_symbols",
        lambda timeout_seconds: ["WMT", "AAPL"],
    )

    symbols, _ = universe.build_universe(
        include_sp500=True,
        extra_symbols=["WMT", "ARM"],
    )

    assert symbols == ["AAPL", "ARM"]
