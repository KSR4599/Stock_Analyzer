from __future__ import annotations

import pandas as pd
import pytest

from stock_analyzer.providers.yfinance_provider import YFinanceProvider, _clean_frame


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [10.0, 11.0], "volume": [1000, 1200]},
        index=pd.to_datetime(["2026-06-16", "2026-06-17"]),
    )


def test_missing_symbols_are_retried_in_smaller_batches(monkeypatch) -> None:
    provider = YFinanceProvider(
        max_symbols_per_batch=3,
        retry_batch_size=2,
        max_single_symbol_retries=1,
    )
    calls: list[list[str]] = []

    def fake_download(batch, period, interval):
        calls.append(batch)
        if batch == ["A", "B", "C"]:
            return {"A": _history()}
        if batch == ["B", "C"]:
            return {"B": _history()}
        return {}

    monkeypatch.setattr(provider, "_download_batch", fake_download)

    histories = provider.get_history(["A", "B", "C"], "1y", "1d")

    assert set(histories) == {"A", "B"}
    assert calls == [["A", "B", "C"], ["B", "C"], ["C"]]
    assert provider.last_health.coverage_pct == pytest.approx(200 / 3)
    assert provider.last_health.failed_symbols == ("C",)
    assert provider.last_health.retry_requests == 2


def test_clean_frame_rejects_invalid_rows_and_duplicate_dates() -> None:
    frame = pd.DataFrame(
        {
            "Close": [10.0, -1.0, 12.0],
            "Volume": [100.0, 100.0, -2.0],
        },
        index=pd.to_datetime(["2026-06-16", "2026-06-17", "2026-06-17"]),
    )

    cleaned = _clean_frame(frame)

    assert list(cleaned["close"]) == [10.0]
