from __future__ import annotations

from datetime import datetime

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import SignalContribution


class CompositeCatalystProvider(CatalystProvider):
    name = "multi"

    def __init__(
        self,
        providers: list[tuple[CatalystProvider, int | None]],
    ) -> None:
        self.providers = providers

    def set_market_histories(self, histories: dict[str, object]) -> None:
        for provider, _ in self.providers:
            setter = getattr(provider, "set_market_histories", None)
            if callable(setter):
                setter(histories)

    def fetch_signals(
        self,
        symbols: list[str],
        run_at: datetime,
    ) -> dict[str, CatalystSignal]:
        collected: dict[str, list[CatalystSignal]] = {symbol: [] for symbol in symbols}
        for provider, limit in self.providers:
            provider_symbols = symbols if limit is None else symbols[:limit]
            try:
                signals = provider.fetch_signals(provider_symbols, run_at)
            except Exception as exc:
                for symbol in provider_symbols:
                    collected[symbol].append(
                        CatalystSignal(
                            symbol=symbol,
                            provider=provider.name,
                            risks=[
                                f"{provider.name} provider failed: {type(exc).__name__}"
                            ],
                        )
                    )
                continue
            for symbol, signal in signals.items():
                collected.setdefault(symbol, []).append(signal)

        result: dict[str, CatalystSignal] = {}
        for symbol in symbols:
            signals = collected.get(symbol, [])
            contributions: list[SignalContribution] = []
            reasons: list[str] = []
            risks: list[str] = []
            events: list[str] = []
            news_items = []
            fundamental_snapshot = None
            market_context = None
            for signal in signals:
                if signal.contributions:
                    contributions.extend(signal.contributions)
                elif signal.score_delta:
                    contributions.append(
                        SignalContribution(
                            category="news",
                            score_delta=signal.score_delta,
                            confidence=signal.confidence,
                            source=signal.provider,
                            summary=f"Legacy {signal.provider} catalyst contribution.",
                            event_id=f"legacy-{signal.provider}-{symbol}",
                        )
                    )
                reasons.extend(signal.reasons)
                risks.extend(signal.risks)
                events.extend(signal.events)
                news_items.extend(signal.news_items)
                fundamental_snapshot = signal.fundamental_snapshot or fundamental_snapshot
                market_context = signal.market_context or market_context
            result[symbol] = aggregate_signal(
                symbol=symbol,
                provider=self.name,
                contributions=contributions,
                reasons=reasons,
                risks=risks,
                events=events,
                news_items=news_items,
                fundamental_snapshot=fundamental_snapshot,
                market_context=market_context,
            )
        return result
