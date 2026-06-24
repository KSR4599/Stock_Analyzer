from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import plistlib

import pandas as pd
import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import PageBreak, SimpleDocTemplate, Table, TableStyle

from stock_analyzer.app import (
    preview_portfolio_import,
    print_portfolio_stability,
    run_portfolio,
)
from stock_analyzer.config import Settings
from stock_analyzer.database import StockDatabase
from stock_analyzer.models import StockScore
from stock_analyzer.portfolio import assess_position, format_portfolio_report
from stock_analyzer.portfolio_models import PortfolioPolicy, PortfolioPosition
from stock_analyzer.portfolio_pdf import (
    PARSER_VERSION,
    PortfolioImportError,
    parse_fidelity_positions_csv,
    parse_fidelity_positions_pdf,
)
from stock_analyzer.telegram import TelegramSendError, _split_message


SENSITIVE_MARKERS = [
    "Z068" + "09604",
    "123-" + "45-6789",
    "RSUS " + "USTEC",
    "GRANT-" + "SECRET-999",
]


def _synthetic_statement(path, malformed: bool = False) -> None:
    document = SimpleDocTemplate(str(path), pagesize=letter)
    elements = []
    elements.append(Table([["Positions - As of Jun-18-2026 9:07 p.m. ET"]]))
    elements.append(
        Table(
            [
                [
                    "Individual - TOD " + SENSITIVE_MARKERS[0],
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "Symbol",
                    "Last price",
                    "Last change",
                    "Today $",
                    "Today %",
                    "",
                    "Total gain $",
                    "Total gain %",
                    "Current value",
                    "% account",
                    "Quantity",
                    "Average",
                    "cost basis",
                ],
                [
                    "TEST\nTEST CORP",
                    "$10.00",
                    "+$1.00",
                    "+$1.00",
                    "+1%",
                    "",
                    "+$20.00",
                    "+20%",
                    "$100.00" if not malformed else "$999.00",
                    "1%",
                    "10",
                    "$8",
                    ".00",
                ],
                [
                    "VOO\nINDEX ETF",
                    "$100.00",
                    "+$1.00",
                    "+$1.00",
                    "+1%",
                    "",
                    "+$10.00",
                    "+10%",
                    "$200.00",
                    "2%",
                    "2",
                    "$95",
                    ".00",
                ],
                [
                    "WMT\nWALMART RSU",
                    "$100.00",
                    "+$1.00",
                    "+$1.00",
                    "+1%",
                    "",
                    "+$20.00",
                    "+10%",
                    "$200.00",
                    "2%",
                    "2",
                    "$90",
                    ".00",
                ],
                [
                    "FCASH\nHELD IN FCASH",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "$50,000.00",
                    "50%",
                    "",
                    "",
                    "",
                ],
            ],
            colWidths=[70, 42, 42, 42, 42, 10, 48, 45, 50, 38, 40, 35, 35],
        )
    )
    elements.append(PageBreak())
    elements.append(
        Table(
            [
                [
                    "Symbol",
                    "Last price",
                    "Last change",
                    "Today $",
                    "Today %",
                    "",
                    "Total gain $",
                    "Total gain %",
                    "Current value",
                    "% account",
                    "Quantity",
                    "Average",
                    "cost basis",
                ],
                [
                    "TEST\nTEST CORP",
                    "$10.00",
                    "+$1.00",
                    "+$1.00",
                    "+1%",
                    "",
                    "+$10.00",
                    "+10%",
                    "$50.00",
                    "1%",
                    "5",
                    "$8",
                    ".00",
                ],
                ["Pending activity", "", "", "", "", "", "", "", "-$2,000", "", "", "", ""],
                ["Account total", "", "", "", "", "", "", "", "$150,000", "", "", "", ""],
            ],
            colWidths=[70, 42, 42, 42, 42, 10, 48, 45, 50, 38, 40, 35, 35],
        )
    )
    elements.append(
        Table([["Stock Plans"], [SENSITIVE_MARKERS[2]], [SENSITIVE_MARKERS[3]]])
    )
    elements.append(Table([["SSN " + SENSITIVE_MARKERS[1]]]))
    for element in elements:
        if isinstance(element, Table):
            element.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTSIZE", (0, 0), (-1, -1), 5),
                    ]
                )
            )
    document.build(elements)


def _score(
    symbol: str = "TEST",
    score: float = 85,
    action: str = "candidate",
    distance_ema50: float = 5,
    relative_21d: float = 10,
    volatility: float = 40,
    atr: float = 4,
) -> StockScore:
    return StockScore(
        symbol=symbol,
        score=score,
        last_price=10,
        action=action,
        suggested_amount=250 if action == "candidate" else 0,
        metrics={
            "distance_from_ema_50_pct": distance_ema50,
            "distance_from_ema_21_pct": 5,
            "relative_to_spy_21d_pct": relative_21d,
            "volatility_20d_annualized_pct": volatility,
            "atr_14d_pct": atr,
            "return_5d_pct": 4,
        },
    )


def _history(last: float = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [9.5, last], "volume": [1000, 1100]},
        index=pd.to_datetime(["2026-06-17", "2026-06-18"]),
    )


def test_pdf_parser_aggregates_and_excludes_sensitive_sections(tmp_path) -> None:
    path = tmp_path / "statement.pdf"
    _synthetic_statement(path)

    result = parse_fidelity_positions_pdf(path)

    assert result.statement_date.isoformat() == "2026-06-18"
    assert [position.symbol for position in result.positions] == ["TEST", "VOO"]
    test = result.positions[0]
    assert test.quantity == 15
    assert test.average_cost == 8


def test_csv_parser_aggregates_and_excludes_non_analysis_rows(tmp_path) -> None:
    path = tmp_path / "Portfolio_Positions_Jun-24-2026.csv"
    path.write_text(
        "\n".join(
            [
                "Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,Cost Basis Total,Average Cost Basis,Type",
                "Z06809604,Private,TEST,Test Corp,2,$10.00,$20.00,$16.00,$8.00,Stock",
                "Z06809604,Private,TEST,Test Corp,3,$10.00,$30.00,$27.00,$9.00,Stock",
                "Z06809604,Private,VOO,Vanguard,1,$100.00,$100.00,$95.00,$95.00,ETF",
                "Z06809604,Private,WMT,Walmart RSU,5,$100.00,$500.00,$400.00,$80.00,Stock",
                "Z06809604,Private,FCASH**,Cash,100,$1.00,$100.00,$100.00,$1.00,Cash",
                "Z06809604,Private,Pending activity,Pending,,,,,,Other",
            ]
        ),
        encoding="utf-8",
    )

    result = parse_fidelity_positions_csv(path)

    assert result.statement_date.isoformat() == "2026-06-24"
    assert [position.symbol for position in result.positions] == ["TEST", "VOO"]
    test = result.positions[0]
    assert test.quantity == 5
    assert test.average_cost == 8.6


def test_pdf_parser_fails_closed_on_arithmetic_mismatch(tmp_path) -> None:
    path = tmp_path / "statement.pdf"
    _synthetic_statement(path, malformed=True)

    with pytest.raises(PortfolioImportError, match="ROW_VALIDATION_FAILED"):
        parse_fidelity_positions_pdf(path)


def test_blocked_import_writes_no_portfolio_rows(tmp_path) -> None:
    path = tmp_path / "statement.pdf"
    _synthetic_statement(path, malformed=True)
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()

    with pytest.raises(PortfolioImportError) as exc_info:
        parse_fidelity_positions_pdf(path)

    assert all(marker not in str(exc_info.value) for marker in SENSITIVE_MARKERS)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM portfolio_imports").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 0


def test_persistence_allowlist_rejects_sensitive_or_ambiguous_input(tmp_path) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()

    with pytest.raises(ValueError, match="data-minimization policy"):
        database.create_portfolio_preview(
            "2026-06-18",
            PARSER_VERSION,
            [PortfolioPosition(SENSITIVE_MARKERS[0], 1, 10)],
        )

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM portfolio_imports").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 0


def test_preview_persists_only_sanitized_allowlist(tmp_path, capsys) -> None:
    path = tmp_path / "statement.pdf"
    _synthetic_statement(path)
    parsed = parse_fidelity_positions_pdf(path)
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()

    import_id, _ = database.create_portfolio_preview(
        parsed.statement_date.isoformat(),
        PARSER_VERSION,
        parsed.positions,
    )

    database_bytes = database.path.read_bytes()
    captured = capsys.readouterr()
    combined = database_bytes + captured.out.encode() + captured.err.encode()
    for marker in SENSITIVE_MARKERS:
        assert marker.encode() not in combined
    assert database.get_portfolio_positions(import_id) == parsed.positions
    with database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(portfolio_positions)"
            ).fetchall()
        }
    assert columns == {
        "import_id",
        "symbol",
        "quantity",
        "average_cost",
        "classification",
    }


def test_import_cli_output_and_logs_never_include_source_markers(
    tmp_path,
    capsys,
    caplog,
) -> None:
    path = tmp_path / "statement.pdf"
    _synthetic_statement(path)
    settings = Settings(db_path=tmp_path / "portfolio.sqlite3")

    preview_portfolio_import(settings, pdf_path=str(path))

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    for marker in SENSITIVE_MARKERS:
        assert marker not in combined
    assert str(path) not in combined


def test_preview_apply_is_atomic_and_rejects_stale_preview(tmp_path) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()
    first, _ = database.create_portfolio_preview(
        "2026-06-18",
        PARSER_VERSION,
        [PortfolioPosition("AAA", 1, 10)],
    )
    database.apply_portfolio_preview(first)
    stale, _ = database.create_portfolio_preview(
        "2026-06-19",
        PARSER_VERSION,
        [PortfolioPosition("AAA", 2, 10)],
    )
    current, _ = database.create_portfolio_preview(
        "2026-06-20",
        PARSER_VERSION,
        [PortfolioPosition("AAA", 3, 10)],
    )
    database.apply_portfolio_preview(current)

    with pytest.raises(ValueError, match="changed after"):
        database.apply_portfolio_preview(stale)
    assert database.get_portfolio_positions()[0].quantity == 3


def test_wmt_is_excluded_even_when_database_is_called_directly(tmp_path) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()
    import_id, _ = database.create_portfolio_preview(
        "2026-06-18",
        PARSER_VERSION,
        [
            PortfolioPosition("TEST", 1, 8),
            PortfolioPosition("WMT", 942.34, 97.1565),
        ],
    )

    assert database.get_portfolio_positions(import_id) == [
        PortfolioPosition("TEST", 1, 8)
    ]
    with pytest.raises(ValueError, match="excluded"):
        database.set_portfolio_policy("WMT", None, False, True)


def test_initialize_purges_existing_wmt_and_recalculates_totals(tmp_path) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()
    import_id, _ = database.create_portfolio_preview(
        "2026-06-18",
        PARSER_VERSION,
        [PortfolioPosition("TEST", 1, 8)],
    )
    database.apply_portfolio_preview(import_id)
    run_id = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 18, tzinfo=timezone.utc),
        100,
        False,
        110,
    )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO portfolio_positions (
                import_id, symbol, quantity, average_cost, classification
            ) VALUES (?, 'WMT', 10, 9, 'adaptive')
            """,
            (import_id,),
        )
        connection.execute(
            """
            INSERT INTO portfolio_assessments (
                run_id, symbol, action, classification, current_price,
                current_value, weight_pct, return_from_cost_pct,
                daily_return_pct, return_5d_pct, score, reasons_text, risks_text
            ) VALUES (?, 'TEST', 'HOLD', 'adaptive', 10, 10, 10, 25,
                      0, 0, 50, '', '')
            """,
            (run_id,),
        )
        connection.execute(
            """
            INSERT INTO portfolio_assessments (
                run_id, symbol, action, classification, current_price,
                current_value, weight_pct, return_from_cost_pct,
                daily_return_pct, return_5d_pct, score, reasons_text, risks_text
            ) VALUES (?, 'WMT', 'WATCH', 'adaptive', 10, 100, 90, 11,
                      0, 0, 50, '', '')
            """,
            (run_id,),
        )

    database.initialize()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM portfolio_positions WHERE symbol = 'WMT'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM portfolio_assessments WHERE symbol = 'WMT'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT total_invested_value FROM portfolio_monitor_runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0] == 10


def test_action_rules_and_degraded_suppression() -> None:
    policy = PortfolioPolicy("TEST", None, False, True)
    buy = assess_position(
        PortfolioPosition("TEST", 1, 8),
        _score(),
        _history(),
        policy,
        weight_pct=5,
        degraded=False,
    )
    exit_review = assess_position(
        PortfolioPosition("TEST", 1, 14),
        _score(action="skip", distance_ema50=-10, relative_21d=-12),
        _history(),
        policy,
        weight_pct=5,
        degraded=False,
    )
    degraded = assess_position(
        PortfolioPosition("TEST", 1, 8),
        _score(),
        _history(),
        policy,
        weight_pct=5,
        degraded=True,
    )

    assert buy.action == "BUY-MORE REVIEW"
    assert exit_review.action == "EXIT REVIEW"
    assert degraded.action == "WATCH"


def test_buy_more_is_suppressed_for_extended_high_volatility_position() -> None:
    assessment = assess_position(
        PortfolioPosition("TEST", 1, 7),
        _score(volatility=95, atr=8),
        _history(),
        PortfolioPolicy("TEST", None, False, True),
        weight_pct=5,
        degraded=False,
    )

    assert assessment.action == "WATCH"
    assert any("too extended" in risk for risk in assessment.risks)


def test_shadow_risk_is_context_only_and_cannot_drive_action() -> None:
    from stock_analyzer.catalysts.base import CatalystSignal

    assessment = assess_position(
        PortfolioPosition("TEST", 1, 8),
        _score(score=60, action="skip"),
        _history(),
        PortfolioPolicy("TEST", None, False, True),
        weight_pct=5,
        degraded=False,
        shadow_context=CatalystSignal(
            symbol="TEST",
            score_delta=-10,
            risks=["Bankruptcy risk from an unactivated shadow provider."],
        ),
    )

    assert assessment.action == "HOLD"
    assert any("Shadow context:" in risk for risk in assessment.risks)


def test_sec_144_is_watch_not_exit_without_critical_risk() -> None:
    score = _score(score=10, action="skip", distance_ema50=-10, relative_21d=-12)
    score.risks.append(
        "Recent SEC 144 filing may indicate proposed insider or affiliate share sales."
    )
    assessment = assess_position(
        PortfolioPosition("TEST", 1, 8),
        score,
        _history(),
        PortfolioPolicy("TEST", None, False, True),
        weight_pct=5,
        degraded=False,
    )

    assert assessment.action == "WATCH"
    assert any("cautionary filing" in risk for risk in assessment.risks)


def test_insufficient_history_uses_latest_price_without_becoming_actionable() -> None:
    assessment = assess_position(
        PortfolioPosition("DRAM", 12, 50),
        StockScore(
            symbol="DRAM",
            score=0,
            last_price=0,
            action="skip",
            suggested_amount=0,
            risks=["Need at least 90 trading days of price history."],
        ),
        _history(last=75),
        PortfolioPolicy("DRAM", "thematic_etf", False, True),
        weight_pct=2,
        degraded=False,
    )

    assert assessment.current_price == 75
    assert assessment.current_value == 900
    assert assessment.action != "BUY-MORE REVIEW"


def test_portfolio_report_contains_only_sanitized_position_fields() -> None:
    position = PortfolioPosition("TEST", 2, 8)
    assessment = assess_position(
        position,
        _score(),
        _history(),
        PortfolioPolicy("TEST", None, False, True),
        weight_pct=100,
        degraded=False,
    )

    report = format_portfolio_report(
        datetime(2026, 6, 18, tzinfo=timezone.utc),
        {"TEST": position},
        [assessment],
        coverage_pct=100,
        degraded=False,
    )

    assert "TEST" in report
    assert "Qty 2" in report
    assert "📊 PORTFOLIO PULSE" in report
    assert "🚦 ACTION SUMMARY" in report
    assert "📋 COMPLETE PORTFOLIO" in report
    for marker in SENSITIVE_MARKERS:
        assert marker not in report


def test_complete_portfolio_report_chunks_safely() -> None:
    positions = {
        f"T{i:02d}": PortfolioPosition(f"T{i:02d}", 1, 8)
        for i in range(23)
    }
    assessments = [
        assess_position(
            position,
            _score(symbol=symbol),
            _history(),
            PortfolioPolicy(symbol, None, False, True),
            weight_pct=100 / 23,
            degraded=False,
        )
        for symbol, position in positions.items()
    ]
    report = format_portfolio_report(
        datetime(2026, 6, 18, tzinfo=timezone.utc),
        positions,
        assessments,
        coverage_pct=100,
        degraded=False,
    )

    chunks = _split_message(report)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_portfolio_report_highlights_action_transitions() -> None:
    position = PortfolioPosition("TEST", 2, 8)
    assessment = assess_position(
        position,
        _score(),
        _history(),
        PortfolioPolicy("TEST", None, False, True),
        weight_pct=5,
        degraded=False,
    )

    report = format_portfolio_report(
        datetime(2026, 6, 18, tzinfo=timezone.utc),
        {"TEST": position},
        [assessment],
        coverage_pct=100,
        degraded=False,
        previous_actions={"TEST": "WATCH"},
    )

    assert "🔄 CHANGES SINCE LAST REVIEW" in report
    assert "• TEST: 🟡 WATCH → 🔵 BUY-MORE" in report
    assert "↪️ Previous: 🟡 WATCH" in report


def test_portfolio_stability_ignores_clustered_and_degraded_runs(
    tmp_path,
    capsys,
) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()
    import_id, _ = database.create_portfolio_preview(
        "2026-06-18",
        PARSER_VERSION,
        [PortfolioPosition("TEST", 1, 8)],
    )
    database.apply_portfolio_preview(import_id)
    run_1 = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 18, 8, tzinfo=timezone.utc),
        100,
        False,
        10,
    )
    run_2 = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 18, 8, 10, tzinfo=timezone.utc),
        100,
        False,
        10,
    )
    run_3 = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 18, 11, tzinfo=timezone.utc),
        50,
        True,
        10,
    )
    run_4 = database.create_portfolio_monitor_run(
        import_id,
        datetime(2026, 6, 18, 14, tzinfo=timezone.utc),
        100,
        False,
        10,
    )
    for run_id, action in [
        (run_1, "HOLD"),
        (run_2, "WATCH"),
        (run_3, "EXIT REVIEW"),
        (run_4, "WATCH"),
    ]:
        assessment = assess_position(
            PortfolioPosition("TEST", 1, 8),
            _score(),
            _history(),
            PortfolioPolicy("TEST", None, False, True),
            weight_pct=100,
            degraded=False,
        )
        database.insert_portfolio_assessments(
            run_id,
            [
                assessment.__class__(
                    **{**vars(assessment), "action": action}
                )
            ],
        )

    result = print_portfolio_stability(
        Settings(db_path=database.path),
        runs=10,
        min_gap_hours=2,
    )

    assert result["eligible_runs"] == 2
    assert result["span_hours"] == pytest.approx(5.8333, rel=1e-3)
    assert result["symbols"][0]["transitions"] == 0
    assert "collecting evidence" in capsys.readouterr().out


def test_portfolio_analysis_survives_telegram_delivery_failure(
    tmp_path,
    monkeypatch,
) -> None:
    database = StockDatabase(tmp_path / "portfolio.sqlite3")
    database.initialize()
    import_id, _ = database.create_portfolio_preview(
        "2026-06-18",
        PARSER_VERSION,
        [PortfolioPosition("TEST", 2, 8)],
    )
    database.apply_portfolio_preview(import_id)

    class FakeProvider:
        def get_history(self, symbols, period, interval):
            return {symbol: _history() for symbol in symbols}

    class FakeCatalystProvider:
        def fetch_signals(self, symbols, as_of):
            return {}

        def set_market_histories(self, histories):
            return None

    class FailingTelegram:
        def validate_live_config(self):
            return None

        def send(self, message, message_kind="message"):
            raise TelegramSendError("ConnectionError")

        def send_document(self, document, filename, caption, document_kind):
            raise TelegramSendError("ConnectionError")

    monkeypatch.setattr("stock_analyzer.app.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "stock_analyzer.app.build_catalyst_provider",
        lambda settings, state_store=None: FakeCatalystProvider(),
    )
    monkeypatch.setattr(
        "stock_analyzer.app.rank_symbols",
        lambda **kwargs: [_score()],
    )
    monkeypatch.setattr(
        "stock_analyzer.app.build_telegram_sender",
        lambda settings: FailingTelegram(),
    )

    report = run_portfolio(
        Settings(db_path=database.path, dry_run=False, min_market_coverage_pct=90)
    )
    status = database.get_portfolio_status()

    assert "PORTFOLIO PULSE" in report
    assert status["latest_run"]["analysis_status"] == "completed"
    assert status["latest_run"]["notification_status"] == "failed"
    assert status["latest_run"]["notification_message"] == "ConnectionError"


def test_portfolio_launch_agent_is_isolated() -> None:
    project = Path(__file__).resolve().parents[1]
    with (project / "deploy/launchd/com.stock-analyzer.portfolio.template.plist").open(
        "rb"
    ) as handle:
        portfolio = plistlib.load(handle)
    with (project / "deploy/launchd/com.stock-analyzer.template.plist").open(
        "rb"
    ) as handle:
        production = plistlib.load(handle)
    with (project / "deploy/launchd/com.stock-analyzer.shadow.template.plist").open(
        "rb"
    ) as handle:
        shadow = plistlib.load(handle)

    assert portfolio["Label"] == "com.stock-analyzer.portfolio"
    assert portfolio["StartInterval"] == 10800
    assert portfolio["Umask"] == 63
    assert "portfolio-run" in portfolio["ProgramArguments"]
    assert "--live" in portfolio["ProgramArguments"]
    assert len({portfolio["Label"], production["Label"], shadow["Label"]}) == 3
