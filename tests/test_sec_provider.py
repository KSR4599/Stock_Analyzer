from __future__ import annotations

from datetime import datetime

from stock_analyzer.catalysts.sec_provider import build_sec_signal


def _submission(forms: list[str], dates: list[str]) -> dict:
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": [f"0000000000-26-00000{index}" for index, _ in enumerate(forms)],
                "primaryDocument": ["primary.htm" for _ in forms],
                "primaryDocDescription": [f"{form} filing" for form in forms],
            }
        }
    }


def test_sec_signal_boosts_recent_8k() -> None:
    signal = build_sec_signal(
        symbol="MOON",
        company_name="Moon Corp",
        submission=_submission(["8-K"], ["2026-06-14"]),
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
