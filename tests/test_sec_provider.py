from __future__ import annotations

from datetime import datetime

from stock_analyzer.catalysts.sec_provider import build_sec_signal


def _submission(
    forms: list[str],
    dates: list[str],
    items: list[str] | None = None,
) -> dict:
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": [f"0000000000-26-00000{index}" for index, _ in enumerate(forms)],
                "primaryDocument": ["primary.htm" for _ in forms],
                "primaryDocDescription": [f"{form} filing" for form in forms],
                "items": items or ["" for _ in forms],
            }
        }
    }


def test_sec_signal_boosts_recent_8k() -> None:
    signal = build_sec_signal(
        symbol="MOON",
        company_name="Moon Corp",
        submission=_submission(["8-K"], ["2026-06-14"], ["1.01"]),
        run_at=datetime(2026, 6, 15, 12, 0, 0),
    )

    assert signal.score_delta > 0
    assert any("8-K" in reason for reason in signal.reasons)
    assert signal.events


def test_sec_signal_penalizes_recent_offering_filing() -> None:
    signal = build_sec_signal(
        symbol="DILUTE",
        company_name="Dilute Corp",
        submission=_submission(["424B5"], ["2026-06-14"]),
        run_at=datetime(2026, 6, 15, 12, 0, 0),
    )

    assert signal.score_delta < 0
    assert any("offering" in risk for risk in signal.risks)


def test_sec_signal_ignores_old_filings() -> None:
    signal = build_sec_signal(
        symbol="OLD",
        company_name="Old Corp",
        submission=_submission(["8-K"], ["2026-05-01"]),
        run_at=datetime(2026, 6, 15, 12, 0, 0),
    )

    assert signal.score_delta == 0
    assert not signal.events


def test_sec_scores_only_open_market_form4_transactions() -> None:
    signal = build_sec_signal(
        symbol="BUY",
        company_name="Buy Corp",
        submission=_submission([], []),
        run_at=datetime(2026, 6, 15, 12, 0, 0),
        insider_transactions=[
            {
                "accession": "one",
                "code": "P",
                "shares": 1000,
                "price": 100,
                "acquired_disposed": "A",
                "value": 100_000,
            },
            {
                "accession": "two",
                "code": "A",
                "shares": 10_000,
                "price": 0,
                "acquired_disposed": "A",
                "value": 0,
            },
        ],
    )

    assert signal.score_delta > 0
    assert any("insider purchases" in reason.lower() for reason in signal.reasons)


def test_sec_8k_delisting_item_is_negative() -> None:
    signal = build_sec_signal(
        symbol="RISK",
        company_name="Risk Corp",
        submission=_submission(["8-K"], ["2026-06-14"], ["3.01"]),
        run_at=datetime(2026, 6, 15, 12, 0, 0),
    )

    assert signal.score_delta < 0
    assert any("delisting" in risk.lower() for risk in signal.risks)
