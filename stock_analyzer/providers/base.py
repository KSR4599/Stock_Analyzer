from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    name: str

    @abstractmethod
    def get_history(
        self,
        symbols: list[str],
        period: str,
        interval: str,
    ) -> dict[str, pd.DataFrame]:
        """Return OHLCV history keyed by canonical symbol."""
