from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import requests

from stock_analyzer.catalysts.aggregation import aggregate_signal
from stock_analyzer.catalysts.base import CatalystProvider, CatalystSignal
from stock_analyzer.catalysts.models import FundamentalSnapshot, SignalContribution


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
)

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
        state_store: Any | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.lookback_days = lookback_days
        self.max_filings = max_filings
        self.state_store = state_store
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
                submission = self._get_cached_json(
                    SEC_SUBMISSIONS_URL.format(cik=cik),
                    cache_key=f"submissions:{cik}",
                    max_age_hours=3,
                )
            except requests.RequestException as exc:
                signals[symbol] = CatalystSignal(
                    symbol=symbol,
                    provider=self.name,
                    risks=[f"SEC submissions fetch failed: {exc}"],
                )
                continue

            company_facts: dict[str, Any] = {}
            try:
                payload = self._get_cached_json(
                    SEC_COMPANY_FACTS_URL.format(cik=cik),
                    cache_key=f"companyfacts:{cik}",
                    max_age_hours=12,
                )
                if isinstance(payload, dict):
                    company_facts = payload
            except requests.RequestException:
                company_facts = {}

            insider_transactions = self._fetch_form4_transactions(
                cik=cik,
                submission=submission,
                max_documents=3,
            )
            signals[symbol] = build_sec_signal(
                symbol=symbol,
                company_name=str(mapping.get("title") or symbol),
                submission=submission,
                run_at=run_at,
                lookback_days=self.lookback_days,
                max_filings=self.max_filings,
                company_facts=company_facts,
                insider_transactions=insider_transactions,
            )

        return signals

    def _get_ticker_map(self) -> dict[str, dict[str, Any]]:
        if self._ticker_map is not None:
            return self._ticker_map

        payload = self._get_cached_json(
            SEC_TICKERS_URL,
            cache_key="ticker_map:all",
            max_age_hours=12,
        )
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

    def _get_text(self, url: str) -> str:
        response = requests.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.text

    def _get_cached_json(
        self,
        url: str,
        cache_key: str,
        max_age_hours: float,
    ) -> Any:
        if self.state_store is not None:
            cached = self.state_store.get_provider_cache(
                self.name,
                cache_key,
                max_age_hours=max_age_hours,
            )
            if cached is not None:
                self.state_store.record_provider_call(
                    self.name,
                    cache_key.split(":", 1)[0],
                    cache_key.split(":", 1)[-1],
                    True,
                    "cache",
                    cache_hit=True,
                    message="cache hit",
                )
                return cached[0]
        try:
            payload = self._get_json(url)
        except requests.RequestException as exc:
            if self.state_store is not None:
                self.state_store.record_provider_call(
                    self.name,
                    cache_key.split(":", 1)[0],
                    cache_key.split(":", 1)[-1],
                    False,
                    type(exc).__name__,
                    message="request failed",
                )
            raise
        if self.state_store is not None:
            self.state_store.set_provider_cache(self.name, cache_key, payload)
            self.state_store.record_provider_call(
                self.name,
                cache_key.split(":", 1)[0],
                cache_key.split(":", 1)[-1],
                True,
                "ok",
                item_count=1,
                message="ok",
            )
        return payload

    def _fetch_form4_transactions(
        self,
        cik: str,
        submission: dict[str, Any],
        max_documents: int,
    ) -> list[dict[str, Any]]:
        recent = submission.get("filings", {}).get("recent", {})
        filings = _recent_filings(recent, max_filings=self.max_filings)
        transactions: list[dict[str, Any]] = []
        for filing in filings:
            if filing.get("form") != "4":
                continue
            accession = str(filing.get("accessionNumber") or "")
            document = str(filing.get("primaryDocument") or "")
            if not accession or not document:
                continue
            cache_key = f"form4:{accession}:{document}"
            text: str | None = None
            if self.state_store is not None:
                cached = self.state_store.get_provider_cache(
                    self.name,
                    cache_key,
                    max_age_hours=72,
                )
                if cached is not None and isinstance(cached[0], str):
                    text = cached[0]
                    self.state_store.record_provider_call(
                        self.name,
                        "form4",
                        accession,
                        True,
                        "cache",
                        item_count=1,
                        cache_hit=True,
                        message="cache hit",
                    )
            if text is None:
                url = SEC_ARCHIVE_URL.format(
                    cik=int(cik),
                    accession=accession.replace("-", ""),
                    document=document,
                )
                try:
                    text = self._get_text(url)
                except requests.RequestException as exc:
                    if self.state_store is not None:
                        self.state_store.record_provider_call(
                            self.name,
                            "form4",
                            accession,
                            False,
                            type(exc).__name__,
                            message="request failed",
                        )
                    continue
                if self.state_store is not None:
                    self.state_store.set_provider_cache(self.name, cache_key, text)
                    self.state_store.record_provider_call(
                        self.name,
                        "form4",
                        accession,
                        True,
                        "ok",
                        item_count=1,
                        message="ok",
                    )
            transactions.extend(parse_form4_transactions(text, accession))
            if len({item["accession"] for item in transactions}) >= max_documents:
                break
        return transactions


def build_sec_signal(
    symbol: str,
    company_name: str,
    submission: dict[str, Any],
    run_at: datetime,
    lookback_days: int = 14,
    max_filings: int = 20,
    company_facts: dict[str, Any] | None = None,
    insider_transactions: list[dict[str, Any]] | None = None,
) -> CatalystSignal:
    recent = submission.get("filings", {}).get("recent", {})
    filings = _recent_filings(recent, max_filings=max_filings)
    cutoff = run_at.date().toordinal() - lookback_days

    contributions: list[SignalContribution] = []
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
        event_id = str(filing.get("accessionNumber") or f"{form}-{filed_at}")
        items = str(filing.get("items") or "")

        if form in RISK_FORMS:
            contributions.append(
                _contribution(
                    -6.0,
                    f"Recent SEC {form} filing may imply financing or offering risk.",
                    event_id,
                )
            )
            risks.append(f"Recent SEC {form} filing may imply financing, offering, or transaction risk.")
            events.append(_with_accession(event, filing))
        elif form in MINOR_RISK_FORMS:
            contributions.append(
                _contribution(-2.0, f"Recent SEC {form} may indicate planned sales.", event_id)
            )
            risks.append(f"Recent SEC {form} filing may indicate proposed insider or affiliate share sales.")
            events.append(_with_accession(event, filing))
        elif form in OWNERSHIP_FORMS:
            contributions.append(
                _contribution(2.0, f"Recent SEC {form} ownership filing.", event_id)
            )
            reasons.append(f"Recent SEC {form} ownership filing may indicate investor interest.")
            events.append(_with_accession(event, filing))
        elif form in INFORMATIONAL_FORMS:
            if form in {"8-K", "6-K"}:
                item_score, item_reason, item_risk = _score_8k_items(items)
                if item_score:
                    contributions.append(
                        _contribution(item_score, item_reason or item_risk, event_id)
                    )
                if item_reason:
                    reasons.append(item_reason)
                elif not item_risk:
                    reasons.append(f"Recent SEC {form} event filing deserves catalyst review.")
                if item_risk:
                    risks.append(item_risk)
            else:
                contributions.append(
                    _contribution(1.0, f"Recent SEC {form} financial filing.", event_id)
                )
                reasons.append(f"Recent SEC {form} financial filing is available for review.")
            events.append(_with_accession(event, filing))
        elif form in INSIDER_FORMS:
            events.append(_with_accession(event, filing))

    insider_contributions, insider_reasons, insider_risks, insider_events = (
        _score_insider_transactions(insider_transactions or [])
    )
    contributions.extend(insider_contributions)
    reasons.extend(insider_reasons)
    risks.extend(insider_risks)
    events.extend(insider_events)

    snapshot, fundamental_contributions, fundamental_reasons, fundamental_risks = (
        build_sec_fundamental_snapshot(
            symbol=symbol,
            run_at=run_at,
            company_facts=company_facts or {},
        )
    )
    contributions.extend(fundamental_contributions)
    reasons.extend(fundamental_reasons)
    risks.extend(fundamental_risks)

    return aggregate_signal(
        symbol=symbol,
        provider="sec",
        contributions=contributions,
        reasons=reasons,
        risks=risks,
        events=events,
        fundamental_snapshot=snapshot,
    )


def _recent_filings(recent: dict[str, list[Any]], max_filings: int) -> list[dict[str, Any]]:
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])
    items = recent.get("items", [])

    filings: list[dict[str, Any]] = []
    for index, form in enumerate(forms[:max_filings]):
        filings.append(
            {
                "form": str(form),
                "filingDate": _list_get(filing_dates, index),
                "accessionNumber": _list_get(accession_numbers, index),
                "primaryDocument": _list_get(primary_documents, index),
                "primaryDocDescription": _list_get(descriptions, index),
                "items": _list_get(items, index),
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


def parse_form4_transactions(
    xml_text: str,
    accession: str = "",
) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    result: list[dict[str, Any]] = []
    for transaction in root.findall(".//nonDerivativeTransaction"):
        code = _xml_text(transaction, ".//transactionCode")
        shares = _to_float(_xml_text(transaction, ".//transactionShares/value"))
        price = _to_float(_xml_text(transaction, ".//transactionPricePerShare/value"))
        acquired_disposed = _xml_text(
            transaction,
            ".//transactionAcquiredDisposedCode/value",
        )
        result.append(
            {
                "accession": accession,
                "code": code,
                "shares": shares,
                "price": price,
                "acquired_disposed": acquired_disposed,
                "value": (shares * price) if shares is not None and price is not None else None,
            }
        )
    return result


def build_sec_fundamental_snapshot(
    symbol: str,
    run_at: datetime,
    company_facts: dict[str, Any],
) -> tuple[FundamentalSnapshot | None, list[SignalContribution], list[str], list[str]]:
    if not company_facts:
        return None, [], [], []
    revenue = _fact_series(
        company_facts,
        ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    )
    net_income = _fact_series(company_facts, ["NetIncomeLoss"])
    cash = _fact_series(
        company_facts,
        ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    )
    debt = _fact_series(
        company_facts,
        ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "LongTermDebt"],
    )
    operating_cash = _fact_series(company_facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _fact_series(
        company_facts,
        ["PaymentsToAcquirePropertyPlantAndEquipment"],
    )
    shares = _fact_series(
        company_facts,
        ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"],
        namespace_order=["dei", "us-gaap"],
    )

    metrics: dict[str, object] = {}
    contributions: list[SignalContribution] = []
    reasons: list[str] = []
    risks: list[str] = []
    revenue_growth = _period_growth(revenue, periods=4)
    if revenue_growth is not None:
        metrics["revenue_growth_yoy_pct"] = round(revenue_growth, 2)
        if revenue_growth >= 20:
            contributions.append(
                _fundamental_contribution(1.5, f"Revenue grew {revenue_growth:.1f}% year over year.")
            )
            reasons.append(f"SEC fundamentals show {revenue_growth:.1f}% year-over-year revenue growth.")
        elif revenue_growth <= -10:
            contributions.append(
                _fundamental_contribution(-1.5, f"Revenue fell {abs(revenue_growth):.1f}% year over year.")
            )
            risks.append(f"SEC fundamentals show revenue down {abs(revenue_growth):.1f}% year over year.")

    latest_revenue = _latest_value(revenue)
    latest_income = _latest_value(net_income)
    if latest_revenue not in (None, 0) and latest_income is not None:
        margin = latest_income / latest_revenue * 100
        metrics["net_margin_pct"] = round(margin, 2)

    latest_cash = _latest_value(cash)
    latest_debt = _latest_value(debt)
    if latest_cash is not None:
        metrics["cash"] = latest_cash
    if latest_debt is not None:
        metrics["debt"] = latest_debt
    if latest_cash is not None and latest_debt is not None:
        metrics["net_cash"] = latest_cash - latest_debt

    latest_ocf = _latest_value(operating_cash)
    latest_capex = _latest_value(capex)
    if latest_ocf is not None and latest_capex is not None:
        metrics["free_cash_flow"] = latest_ocf - abs(latest_capex)
    fcf_growth = _free_cash_flow_growth(operating_cash, capex, periods=4)
    if fcf_growth is not None:
        metrics["free_cash_flow_growth_yoy_pct"] = round(fcf_growth, 2)
        if fcf_growth >= 25:
            contributions.append(
                _fundamental_contribution(
                    1.0,
                    f"Free cash flow improved {fcf_growth:.1f}% year over year.",
                )
            )
            reasons.append(f"Free cash flow improved {fcf_growth:.1f}% year over year.")
        elif fcf_growth <= -25:
            contributions.append(
                _fundamental_contribution(
                    -1.0,
                    f"Free cash flow weakened {abs(fcf_growth):.1f}% year over year.",
                )
            )
            risks.append(f"Free cash flow weakened {abs(fcf_growth):.1f}% year over year.")

    shares_growth = _period_growth(shares, periods=4)
    if shares_growth is not None:
        metrics["shares_growth_yoy_pct"] = round(shares_growth, 2)
        if shares_growth >= 8:
            contributions.append(
                _fundamental_contribution(
                    -2.5,
                    f"Shares outstanding increased {shares_growth:.1f}% year over year.",
                )
            )
            risks.append(f"Shares outstanding increased {shares_growth:.1f}% year over year.")

    snapshot = FundamentalSnapshot(
        symbol=symbol,
        as_of=run_at,
        provider="sec",
        metrics=metrics,
    )
    return snapshot, contributions, reasons, risks


def _score_8k_items(items: str) -> tuple[float, str, str]:
    item_set = {item.strip() for item in items.split(",") if item.strip()}
    if "3.01" in item_set:
        return -5.0, "", "Recent 8-K reports a delisting or listing-compliance event."
    if "3.02" in item_set:
        return -4.0, "", "Recent 8-K reports an unregistered equity sale."
    if "2.03" in item_set:
        return -2.0, "", "Recent 8-K reports a material financing obligation."
    if "1.01" in item_set:
        return 2.0, "Recent 8-K reports a material definitive agreement.", ""
    if "2.02" in item_set:
        return 1.0, "Recent 8-K reports operating or financial results.", ""
    if "5.02" in item_set:
        return 0.0, "", "Recent 8-K reports a leadership or director change."
    return 0.0, "", ""


def _score_insider_transactions(
    transactions: list[dict[str, Any]],
) -> tuple[list[SignalContribution], list[str], list[str], list[str]]:
    contributions: list[SignalContribution] = []
    reasons: list[str] = []
    risks: list[str] = []
    events: list[str] = []
    purchase_value = sum(
        float(item.get("value") or 0)
        for item in transactions
        if item.get("code") == "P" and item.get("acquired_disposed") == "A"
    )
    sale_value = sum(
        float(item.get("value") or 0)
        for item in transactions
        if item.get("code") == "S" and item.get("acquired_disposed") == "D"
    )
    if purchase_value >= 50_000:
        score = 2.0 if purchase_value >= 250_000 else 1.0
        contributions.append(
            _contribution(
                score,
                f"Open-market insider purchases total about ${purchase_value:,.0f}.",
                "form4-open-market-purchases",
            )
        )
        reasons.append(f"Open-market insider purchases total about ${purchase_value:,.0f}.")
        events.append(f"Insider purchases: ${purchase_value:,.0f}")
    if sale_value >= 250_000:
        contributions.append(
            _contribution(
                -1.0,
                f"Open-market insider sales total about ${sale_value:,.0f}.",
                "form4-open-market-sales",
            )
        )
        risks.append(f"Open-market insider sales total about ${sale_value:,.0f}.")
        events.append(f"Insider sales: ${sale_value:,.0f}")
    return contributions, reasons, risks, events


def _contribution(score: float, summary: str, event_id: str) -> SignalContribution:
    return SignalContribution(
        category="filings_insiders",
        score_delta=score,
        confidence=0.08,
        source="sec",
        summary=summary,
        event_id=event_id,
    )


def _fundamental_contribution(score: float, summary: str) -> SignalContribution:
    return SignalContribution(
        category="fundamentals_analyst",
        score_delta=score,
        confidence=0.1,
        source="sec",
        summary=summary,
        event_id=f"sec-fundamental-{summary[:40]}",
    )


def _fact_series(
    company_facts: dict[str, Any],
    tags: list[str],
    namespace_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    facts = company_facts.get("facts", {})
    namespaces = namespace_order or ["us-gaap", "dei", "ifrs-full"]
    for namespace in namespaces:
        namespace_facts = facts.get(namespace, {})
        for tag in tags:
            fact = namespace_facts.get(tag)
            if not isinstance(fact, dict):
                continue
            units = fact.get("units", {})
            for values in units.values():
                if isinstance(values, list):
                    quarterly = [
                        item
                        for item in values
                        if isinstance(item, dict)
                        and item.get("form") in {"10-Q", "10-K", "20-F", "40-F"}
                        and item.get("val") is not None
                    ]
                    if quarterly:
                        return _dedupe_fact_periods(quarterly)
    return []


def _dedupe_fact_periods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_period: dict[tuple[object, object], dict[str, Any]] = {}
    for item in items:
        key = (item.get("end"), item.get("fp"))
        existing = by_period.get(key)
        if existing is None or str(item.get("filed") or "") > str(existing.get("filed") or ""):
            by_period[key] = item
    return sorted(by_period.values(), key=lambda item: str(item.get("end") or ""))


def _period_growth(series: list[dict[str, Any]], periods: int) -> float | None:
    if len(series) <= periods:
        return None
    latest = _to_float(series[-1].get("val"))
    prior = _to_float(series[-1 - periods].get("val"))
    if latest is None or prior in (None, 0):
        return None
    return (latest / prior - 1) * 100


def _latest_value(series: list[dict[str, Any]]) -> float | None:
    if not series:
        return None
    return _to_float(series[-1].get("val"))


def _free_cash_flow_growth(
    operating_cash: list[dict[str, Any]],
    capex: list[dict[str, Any]],
    periods: int,
) -> float | None:
    ocf_by_end = {
        str(item.get("end")): _to_float(item.get("val"))
        for item in operating_cash
    }
    capex_by_end = {
        str(item.get("end")): _to_float(item.get("val"))
        for item in capex
    }
    common = sorted(set(ocf_by_end) & set(capex_by_end))
    if len(common) <= periods:
        return None
    latest_end = common[-1]
    prior_end = common[-1 - periods]
    latest_ocf = ocf_by_end[latest_end]
    latest_capex = capex_by_end[latest_end]
    prior_ocf = ocf_by_end[prior_end]
    prior_capex = capex_by_end[prior_end]
    if None in {latest_ocf, latest_capex, prior_ocf, prior_capex}:
        return None
    latest_fcf = float(latest_ocf) - abs(float(latest_capex))
    prior_fcf = float(prior_ocf) - abs(float(prior_capex))
    if prior_fcf <= 0 or latest_fcf <= 0:
        return None
    growth = (latest_fcf / prior_fcf - 1) * 100
    if abs(growth) > 500:
        return None
    return growth


def _xml_text(element: ElementTree.Element, path: str) -> str:
    found = element.find(path)
    return found.text.strip() if found is not None and found.text else ""


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
