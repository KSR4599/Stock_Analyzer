from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

RISK_FORMS = {
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "424B1",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "424B8",
    "SC 13E3",
}

MINOR_RISK_FORMS = {
    "144",
}

INFORMATIONAL_FORMS = {
    "8-K",
    "6-K",
    "10-Q",
    "10-K",
    "20-F",
    "40-F",
}

INSIDER_FORMS = {
    "3",
    "4",
    "5",
}

OWNERSHIP_FORMS = {
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
}


class SecEdgarCatalystProvider(CatalystProvider):
    name = "sec"

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 20.0,
        lookback_days: int = 14,
        max_filings: int = 20,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.lookback_days = lookback_days
        self.max_filings = max_filings
        self._ticker_map: dict[str, dict[str, Any]] | None = None

    def fetch_signals(self, symbols: list[str], run_at: datetime) -> dict[str, CatalystSignal]:
        try:
            ticker_map = self._get_ticker_map()
        except requests.RequestException as exc:
            return {
                symbol: CatalystSignal(
                    symbol=symbol,
                    provider=self.name,
                    risks=[f"SEC ticker map fetch failed: {exc}"],
                )
                for symbol in symbols
            }

        signals: dict[str, CatalystSignal] = {}
        for symbol in symbols:
            mapping = ticker_map.get(symbol.upper())
            if mapping is None:
                signals[symbol] = CatalystSignal(
                    symbol=symbol,
                    provider=self.name,
                    risks=["No SEC CIK mapping found for this ticker."],
                )
                continue

            cik = str(mapping["cik_str"]).zfill(10)
            try:
                submission = self._get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
            except requests.RequestException as exc:
                signals[symbol] = CatalystSignal(
                    symbol=symbol,
                    provider=self.name,
                    risks=[f"SEC submissions fetch failed: {exc}"],
                )
                continue

            signals[symbol] = build_sec_signal(
                symbol=symbol,
                company_name=str(mapping.get("title") or symbol),
                submission=submission,
                run_at=run_at,
                lookback_days=self.lookback_days,
                max_filings=self.max_filings,
            )

        return signals

    def _get_ticker_map(self) -> dict[str, dict[str, Any]]:
        if self._ticker_map is not None:
            return self._ticker_map

        payload = self._get_json(SEC_TICKERS_URL)
        ticker_map: dict[str, dict[str, Any]] = {}
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").upper()
            if ticker:
                ticker_map[ticker] = item

        self._ticker_map = ticker_map
        return ticker_map

    def _get_json(self, url: str) -> Any:
        response = requests.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": url.split("/")[2],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


def build_sec_signal(
    symbol: str,
    company_name: str,
    submission: dict[str, Any],
    run_at: datetime,
    lookback_days: int = 14,
    max_filings: int = 20,
) -> CatalystSignal:
    recent = submission.get("filings", {}).get("recent", {})
    filings = _recent_filings(recent, max_filings=max_filings)
    cutoff = run_at.date().toordinal() - lookback_days

    score = 0.0
    confidence = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []

    for filing in filings:
        form = filing.get("form", "")
        filed_at = filing.get("filingDate")
        filed_date = _parse_date(filed_at)
        if filed_date is None or filed_date.toordinal() < cutoff:
            continue

        description = filing.get("primaryDocDescription") or filing.get("primaryDocument") or form
        event = f"SEC {form}: {description} filed {filed_at}"

        if form in RISK_FORMS:
            score -= 6.0
            confidence += 0.08
            risks.append(f"Recent SEC {form} filing may imply financing, offering, or transaction risk.")
            events.append(_with_accession(event, filing))
        elif form in MINOR_RISK_FORMS:
            score -= 2.0
            confidence += 0.05
            risks.append(f"Recent SEC {form} filing may indicate proposed insider or affiliate share sales.")
            events.append(_with_accession(event, filing))
        elif form in OWNERSHIP_FORMS:
            score += 2.0
            confidence += 0.08
            reasons.append(f"Recent SEC {form} ownership filing may indicate investor interest.")
            events.append(_with_accession(event, filing))
        elif form in INFORMATIONAL_FORMS:
            confidence += 0.08
            if form in {"8-K", "6-K"}:
                score += 2.0
                reasons.append(f"Recent SEC {form} event filing deserves catalyst review.")
            else:
                score += 1.0
                reasons.append(f"Recent SEC {form} financial filing is available for review.")
            events.append(_with_accession(event, filing))
        elif form in INSIDER_FORMS:
            confidence += 0.03
            events.append(_with_accession(event, filing))

    return CatalystSignal(
        symbol=symbol,
        score_delta=round(max(min(score, 12), -18), 1),
        confidence=round(max(min(confidence, 0.35), 0.0), 2),
        provider="sec",
        reasons=_dedupe(reasons)[:4],
        risks=_dedupe(risks)[:4],
        events=_dedupe(events)[:5],
    )


def _recent_filings(recent: dict[str, list[Any]], max_filings: int) -> list[dict[str, Any]]:
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    filings: list[dict[str, Any]] = []
    for index, form in enumerate(forms[:max_filings]):
        filings.append(
            {
                "form": str(form),
                "filingDate": _list_get(filing_dates, index),
                "accessionNumber": _list_get(accession_numbers, index),
                "primaryDocument": _list_get(primary_documents, index),
                "primaryDocDescription": _list_get(descriptions, index),
            }
        )
    return filings


def _list_get(items: list[Any], index: int) -> Any:
    if index >= len(items):
        return None
    return items[index]


def _with_accession(event: str, filing: dict[str, Any]) -> str:
    accession = filing.get("accessionNumber", "")
    if accession:
        return f"{event} ({accession})"
    return event


def _parse_date(value: Any) -> datetime.date | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped
