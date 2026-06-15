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
