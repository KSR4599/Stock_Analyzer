from __future__ import annotations

from io import BytesIO
from datetime import datetime
from html import escape
from typing import Iterable

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from stock_analyzer.models import StockScore
from stock_analyzer.portfolio_models import (
    PortfolioAssessment,
    PortfolioEodReport,
    PortfolioPosition,
    PortfolioPriceSnapshot,
)


INK = HexColor("#13221F")
MUTED = HexColor("#66736F")
GREEN = HexColor("#087A61")
GREEN_SOFT = HexColor("#DFF3ED")
BLUE = HexColor("#3167D4")
BLUE_SOFT = HexColor("#E7EEFC")
AMBER = HexColor("#A56800")
AMBER_SOFT = HexColor("#FFF2D7")
RED = HexColor("#B83232")
RED_SOFT = HexColor("#FDE9E8")
LINE = HexColor("#DCE4E1")
WASH = HexColor("#F4F7F6")
ACTION_COLORS = {
    "EXIT REVIEW": RED,
    "TRIM REVIEW": AMBER,
    "BUY-MORE REVIEW": BLUE,
    "WATCH": HexColor("#B69A17"),
    "HOLD": GREEN,
}
CHART_COLORS = [
    GREEN,
    BLUE,
    HexColor("#7246D6"),
    HexColor("#D3731F"),
    HexColor("#BD2354"),
    HexColor("#238CA8"),
    HexColor("#6D768A"),
    HexColor("#B28A13"),
]


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=27,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            "Eyebrow",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=GREEN,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=17,
            textColor=INK,
            spaceBefore=8,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            "BodySmall",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=INK,
        )
    )
    styles.add(
        ParagraphStyle(
            "Muted",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=MUTED,
        )
    )
    styles.add(
        ParagraphStyle(
            "CardTitle",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=INK,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            "Metric",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=17,
            textColor=INK,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "MetricLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=9,
            textColor=MUTED,
            alignment=TA_CENTER,
        )
    )
    return styles


class _Rule(Flowable):
    def __init__(self, width: float, color=LINE, thickness: float = 0.7):
        super().__init__()
        self.width = width
        self.height = thickness
        self.color = color
        self.thickness = thickness

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 0.47 * inch, letter[0] - doc.rightMargin, 0.47 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.3 * inch, "Research only - no automatic trading")
    canvas.drawRightString(
        letter[0] - doc.rightMargin,
        0.3 * inch,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def _doc(buffer: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.46 * inch,
        rightMargin=0.46 * inch,
        topMargin=0.42 * inch,
        bottomMargin=0.62 * inch,
        title=title,
        author="Stock Analyzer",
        subject="Private research alert",
    )


def _header(title: str, run_at: datetime, subtitle: str, styles) -> list[Flowable]:
    return [
        Paragraph("PRIVATE RESEARCH ALERT", styles["Eyebrow"]),
        Paragraph(escape(title), styles["ReportTitle"]),
        Paragraph(
            f"{escape(run_at.strftime('%A, %B %d, %Y - %I:%M %p %Z'))}<br/>"
            f"{escape(subtitle)}",
            styles["Muted"],
        ),
        Spacer(1, 0.12 * inch),
        _Rule(7.55 * inch, GREEN, 1.2),
        Spacer(1, 0.12 * inch),
    ]


def _metric_cards(items: list[tuple[str, str]], styles) -> Table:
    cells = [
        [
            Paragraph(escape(label.upper()), styles["MetricLabel"]),
            Spacer(1, 3),
            Paragraph(escape(value), styles["Metric"]),
        ]
        for label, value in items
    ]
    table = Table([cells], colWidths=[7.45 * inch / len(cells)] * len(cells))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WASH),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.7, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _allocation_chart(assessments: list[PortfolioAssessment]) -> Drawing:
    ordered = sorted(assessments, key=lambda item: item.weight_pct, reverse=True)
    major = ordered[:7]
    other = sum(item.weight_pct for item in ordered[7:])
    labels = [item.symbol for item in major]
    values = [max(0.01, item.weight_pct) for item in major]
    if other > 0:
        labels.append("Other")
        values.append(other)
    drawing = Drawing(260, 165)
    pie = Pie()
    pie.x = 10
    pie.y = 14
    pie.width = 125
    pie.height = 125
    pie.data = values
    pie.labels = ["" for _ in values]
    pie.slices.strokeWidth = 0.3
    pie.slices.strokeColor = colors.white
    for index in range(len(values)):
        pie.slices[index].fillColor = CHART_COLORS[index % len(CHART_COLORS)]
    drawing.add(pie)
    drawing.add(String(150, 137, "Allocation", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))
    y = 116
    for index, (label, value) in enumerate(zip(labels[:7], values[:7])):
        drawing.add(
            String(
                150,
                y,
                f"{label}: {value:.1f}%",
                fontName="Helvetica",
                fontSize=8,
                fillColor=CHART_COLORS[index % len(CHART_COLORS)],
            )
        )
        y -= 16
    return drawing


def _action_chart(counts: dict[str, int]) -> Drawing:
    actions = [
        action
        for action in ["EXIT REVIEW", "TRIM REVIEW", "BUY-MORE REVIEW", "WATCH", "HOLD"]
        if counts.get(action)
    ]
    drawing = Drawing(260, 165)
    if not actions:
        drawing.add(String(25, 80, "No actions available", fillColor=MUTED))
        return drawing
    chart = HorizontalBarChart()
    chart.x = 76
    chart.y = 24
    chart.height = 115
    chart.width = 160
    chart.data = [[counts[action] for action in actions]]
    chart.categoryAxis.categoryNames = [action.replace(" REVIEW", "") for action in actions]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(counts[action] for action in actions) + 1
    chart.valueAxis.valueStep = max(1, chart.valueAxis.valueMax // 4)
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = GREEN
    drawing.add(chart)
    drawing.add(String(76, 148, "Action distribution", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))
    return drawing


def _score_chart(scores: list[StockScore]) -> Drawing:
    visible = scores[:8]
    drawing = Drawing(515, 205)
    if not visible:
        drawing.add(String(25, 100, "No ranked scores available", fillColor=MUTED))
        return drawing
    chart = HorizontalBarChart()
    chart.x = 70
    chart.y = 24
    chart.height = 150
    chart.width = 415
    chart.data = [[max(0, item.score) for item in reversed(visible)]]
    chart.categoryAxis.categoryNames = [item.symbol for item in reversed(visible)]
    chart.categoryAxis.labels.fontName = "Helvetica-Bold"
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = GREEN
    drawing.add(chart)
    drawing.add(String(70, 188, "Top deterministic scores", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))
    return drawing


def _safe_text(items: Iterable[str], limit: int = 2) -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    return " ".join(clean[:limit]) or "No stored detail."


def build_portfolio_alert_pdf(
    run_at: datetime,
    positions: dict[str, PortfolioPosition],
    assessments: list[PortfolioAssessment],
    coverage_pct: float,
    degraded: bool,
    previous_actions: dict[str, str] | None = None,
) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    doc = _doc(buffer, "Portfolio Alert")
    total_value = sum(item.current_value for item in assessments)
    total_cost = sum(
        positions[item.symbol].quantity * positions[item.symbol].average_cost
        for item in assessments
    )
    total_return = (total_value / total_cost - 1) * 100 if total_cost else 0.0
    counts: dict[str, int] = {}
    for item in assessments:
        counts[item.action] = counts.get(item.action, 0) + 1
    previous_actions = previous_actions or {}
    transitions = [
        item
        for item in assessments
        if previous_actions.get(item.symbol)
        and previous_actions[item.symbol] != item.action
    ]

    story: list[Flowable] = _header(
        "Portfolio Alert",
        run_at,
        f"{len(assessments)} positions | {coverage_pct:.1f}% market coverage"
        + (" | ACTIONS SUPPRESSED" if degraded else " | Healthy data"),
        styles,
    )
    story.extend(
        [
            _metric_cards(
                [
                    ("Market value", f"${total_value:,.2f}"),
                    ("Cost basis", f"${total_cost:,.2f}"),
                    ("Return vs cost", f"{total_return:+.2f}%"),
                    (
                        "Priority reviews",
                        str(
                            counts.get("EXIT REVIEW", 0)
                            + counts.get("TRIM REVIEW", 0)
                            + counts.get("BUY-MORE REVIEW", 0)
                        ),
                    ),
                ],
                styles,
            ),
            Spacer(1, 0.16 * inch),
            Table(
                [[_allocation_chart(assessments), _action_chart(counts)]],
                colWidths=[3.72 * inch, 3.72 * inch],
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                        ("INNERGRID", (0, 0), (-1, -1), 0.7, LINE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ]
                ),
            ),
        ]
    )
    story.append(Paragraph("Changes since previous healthy review", styles["Section"]))
    if transitions:
        change_data = [["Symbol", "Previous", "Current"]]
        change_data.extend(
            [
                [
                    item.symbol,
                    previous_actions[item.symbol],
                    item.action,
                ]
                for item in sorted(transitions, key=lambda item: item.symbol)
            ]
        )
        story.append(_standard_table(change_data, [1.1 * inch, 2.8 * inch, 2.8 * inch]))
    else:
        story.append(Paragraph("No action-label changes.", styles["BodySmall"]))

    priority = [
        item
        for item in assessments
        if item.action in {"EXIT REVIEW", "TRIM REVIEW", "BUY-MORE REVIEW"}
    ]
    story.append(Paragraph("Priority reviews", styles["Section"]))
    if not priority:
        story.append(Paragraph("No priority reviews in this run.", styles["BodySmall"]))
    for item in sorted(priority, key=lambda item: (-item.weight_pct, item.symbol)):
        position = positions[item.symbol]
        accent = ACTION_COLORS[item.action]
        body = [
            [
                Paragraph(f"<b>{escape(item.symbol)}</b><br/><font color='{accent.hexval()}'>{escape(item.action)}</font>", styles["CardTitle"]),
                Paragraph(
                    f"<b>${item.current_price:,.2f}</b> price<br/>"
                    f"${position.average_cost:,.2f} average cost<br/>"
                    f"{item.return_from_cost_pct:+.2f}% P/L",
                    styles["BodySmall"],
                ),
                Paragraph(
                    f"<b>{item.weight_pct:.2f}%</b> allocation<br/>"
                    f"${item.current_value:,.2f} value<br/>"
                    f"Score {item.score:.1f}",
                    styles["BodySmall"],
                ),
            ],
            [
                Paragraph(f"<b>Why:</b> {escape(_safe_text(item.reasons, 2))}", styles["BodySmall"]),
                Paragraph(f"<b>Risk:</b> {escape(_safe_text(item.risks, 1))}", styles["BodySmall"]),
                "",
            ],
        ]
        card = Table(body, colWidths=[2.2 * inch, 2.35 * inch, 2.35 * inch])
        card.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1.1, accent),
                    ("SPAN", (0, 1), (0, 1)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), WASH),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.extend([KeepTogether(card), Spacer(1, 8)])

    story.extend([PageBreak(), Paragraph("Complete portfolio", styles["Section"])])
    rows = [["Symbol", "Action", "Qty", "Price", "Value", "Alloc.", "Avg cost", "P/L", "Day", "5D", "Score"]]
    for item in sorted(assessments, key=lambda item: (-item.weight_pct, item.symbol)):
        position = positions[item.symbol]
        rows.append(
            [
                item.symbol,
                item.action.replace(" REVIEW", ""),
                f"{position.quantity:g}",
                f"${item.current_price:,.2f}",
                f"${item.current_value:,.0f}",
                f"{item.weight_pct:.1f}%",
                f"${position.average_cost:,.2f}",
                f"{item.return_from_cost_pct:+.1f}%",
                "n/a" if item.daily_return_pct is None else f"{item.daily_return_pct:+.1f}%",
                "n/a" if item.return_5d_pct is None else f"{item.return_5d_pct:+.1f}%",
                f"{item.score:.0f}",
            ]
        )
    story.append(
        _standard_table(
            rows,
            [
                0.48 * inch,
                0.68 * inch,
                0.46 * inch,
                0.64 * inch,
                0.67 * inch,
                0.47 * inch,
                0.65 * inch,
                0.53 * inch,
                0.48 * inch,
                0.48 * inch,
                0.42 * inch,
            ],
            font_size=6.4,
            repeat_rows=1,
        )
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def build_universe_alert_pdf(
    scores: list[StockScore],
    run_at: datetime,
    provider: str,
    catalyst_provider: str,
    universe_source: str,
    universe_size: int,
    budget: float,
    threshold: float,
    market_requested: int,
    market_received: int,
    market_coverage_pct: float,
    market_degraded: bool,
    market_failures: list[str] | None = None,
    top_n: int = 10,
) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    doc = _doc(buffer, "Universe Alert")
    ranked = scores[: max(1, top_n)]
    alerts = [score for score in scores if score.is_alert]
    top = ranked[0] if ranked else None
    subtitle = (
        f"{universe_size} symbols | {market_received}/{market_requested} market histories "
        f"({market_coverage_pct:.1f}%) | {provider} + {catalyst_provider}"
    )
    story: list[Flowable] = _header("Universe Alert", run_at, subtitle, styles)
    story.extend(
        [
            _metric_cards(
                [
                    ("Candidates", str(len(alerts))),
                    ("Top ranked", top.symbol if top else "None"),
                    ("Top score", f"{top.score:.1f}" if top else "n/a"),
                    ("Starter review", f"${budget:.0f} at {threshold:.0f}+"),
                ],
                styles,
            ),
            Spacer(1, 0.15 * inch),
        ]
    )
    if market_degraded:
        failed = ", ".join((market_failures or [])[:12]) or "benchmark or usable scores unavailable"
        warning = Table(
            [[Paragraph(
                f"<b>DEGRADED MARKET DATA</b><br/>Candidate transitions were suppressed. Missing: {escape(failed)}",
                styles["BodySmall"],
            )]],
            colWidths=[7.45 * inch],
        )
        warning.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), RED_SOFT),
                    ("BOX", (0, 0), (-1, -1), 1, RED),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.extend([warning, Spacer(1, 0.12 * inch)])
    story.extend([_score_chart(ranked), Paragraph("Candidate reviews", styles["Section"])])
    if not alerts:
        story.append(
            Paragraph(
                f"No ${budget:.0f} candidate cleared score {threshold:.1f} in this run.",
                styles["BodySmall"],
            )
        )
    for score in alerts:
        score_text = f"{score.score:.1f}"
        if score.market_score is not None:
            score_text += f" (market {score.market_score:.1f}, catalyst {score.catalyst_score:+.1f})"
        card_data = [
            [
                Paragraph(f"<b>{escape(score.symbol)}</b><br/><font color='{GREEN.hexval()}'>CANDIDATE</font>", styles["CardTitle"]),
                Paragraph(
                    f"<b>Score {escape(score_text)}</b><br/>"
                    f"${score.last_price:,.2f} price<br/>"
                    f"${score.suggested_amount:,.0f} starter review",
                    styles["BodySmall"],
                ),
                Paragraph(
                    f"<b>Setup:</b> {escape(score.setup)}<br/>"
                    f"<b>Risk:</b> {escape(score.risk_level)}<br/>"
                    f"<b>Provider:</b> {escape(score.catalyst_provider)}<br/>"
                    f"<b>Change:</b> {escape(_signal_change_text(score))}<br/>"
                    f"<b>Calibration:</b> {escape(_calibration_text(score))}",
                    styles["BodySmall"],
                ),
            ],
            [
                Paragraph(f"<b>Why:</b> {escape(_safe_text(score.reasons, 3))}", styles["BodySmall"]),
                Paragraph(f"<b>Watchouts:</b> {escape(_safe_text(score.risks, 2))}", styles["BodySmall"]),
                Paragraph(f"<b>Catalysts:</b> {escape(_safe_text(score.catalysts, 2))}", styles["BodySmall"]),
            ],
        ]
        card = Table(card_data, colWidths=[1.7 * inch, 2.55 * inch, 2.7 * inch])
        card.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1.1, GREEN),
                    ("BACKGROUND", (0, 0), (-1, 0), GREEN_SOFT),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.extend([KeepTogether(card), Spacer(1, 8)])

    story.extend([Paragraph("Top ranked names", styles["Section"])])
    rows = [["Rank", "Symbol", "Score", "Move", "Action", "Price", "Setup", "Risk", "Catalyst"]]
    for index, score in enumerate(ranked, start=1):
        rows.append(
            [
                str(index),
                score.symbol,
                f"{score.score:.1f}",
                _compact_move(score),
                score.action.upper(),
                f"${score.last_price:,.2f}",
                score.setup,
                score.risk_level,
                f"{score.catalyst_score:+.1f}",
            ]
        )
    story.append(
        _standard_table(
            rows,
            [
                0.38 * inch,
                0.57 * inch,
                0.55 * inch,
                0.64 * inch,
                0.72 * inch,
                0.68 * inch,
                1.35 * inch,
                0.72 * inch,
                0.65 * inch,
            ],
            font_size=7,
            repeat_rows=1,
        )
    )
    story.extend(
        [
            Spacer(1, 0.12 * inch),
            Paragraph(
                f"Universe source: {escape(universe_source)}. Production SEC evidence only; "
                "shadow evidence is not used for actionable recommendations.",
                styles["Muted"],
            ),
        ]
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def build_portfolio_eod_pdf(report: PortfolioEodReport) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    title = f"End-of-Day Portfolio Report - {report.run_at.strftime('%Y-%m-%d %H:%M %Z')}"
    doc = _doc(buffer, title)
    valid = [snapshot for snapshot in report.snapshots if not snapshot.degraded]
    degraded = [snapshot for snapshot in report.snapshots if snapshot.degraded]
    top_gainers = sorted(valid, key=lambda item: item.move_pct, reverse=True)[:5]
    top_losers = sorted(valid, key=lambda item: item.move_pct)[:5]
    impact = sorted(valid, key=lambda item: abs(item.day_dollar_change), reverse=True)[:6]

    story: list[Flowable] = _header(
        title,
        report.run_at,
        f"{len(valid)}/{len(report.snapshots)} positions covered | source {report.source}"
        + (" | DEGRADED DATA" if report.degraded else " | Healthy data"),
        styles,
    )
    story.extend(
        [
            _metric_cards(
                [
                    ("Market value", f"${report.total_value:,.2f}"),
                    ("Day gains", f"${report.total_gain_dollars:,.2f}"),
                    ("Day losses", f"${report.total_loss_dollars:,.2f}"),
                    ("Net day", f"${report.net_change_dollars:+,.2f}"),
                    ("Net %", f"{report.net_change_pct:+.2f}%"),
                    ("W/L/F", f"{report.winner_count}/{report.loser_count}/{report.flat_count}"),
                ],
                styles,
            ),
            Spacer(1, 0.16 * inch),
            Table(
                [[_eod_waterfall_chart(report), _eod_mover_chart(valid)]],
                colWidths=[3.72 * inch, 3.72 * inch],
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                        ("INNERGRID", (0, 0), (-1, -1), 0.7, LINE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ]
                ),
            ),
        ]
    )
    if degraded:
        failed = ", ".join(f"{item.symbol}: {item.message}" for item in degraded[:8])
        warning = Table(
            [[Paragraph(f"<b>DEGRADED PRICE DATA</b><br/>{escape(failed)}", styles["BodySmall"])]],
            colWidths=[7.45 * inch],
        )
        warning.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), RED_SOFT),
                    ("BOX", (0, 0), (-1, -1), 1, RED),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.extend([Spacer(1, 0.12 * inch), warning])

    story.append(Paragraph("Top movers", styles["Section"]))
    story.append(
        Table(
            [
                [
                    _snapshot_table("Top gainers", top_gainers, styles),
                    _snapshot_table("Top losers", top_losers, styles),
                ]
            ],
            colWidths=[3.72 * inch, 3.72 * inch],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        )
    )
    story.append(Paragraph("Largest dollar impacts", styles["Section"]))
    impact_rows = [["Symbol", "Price", "Move", "$/share", "Position impact", "Value"]]
    for item in impact:
        impact_rows.append(_snapshot_row(item))
    story.append(
        _standard_table(
            impact_rows,
            [0.7 * inch, 0.85 * inch, 0.72 * inch, 0.75 * inch, 1.15 * inch, 0.95 * inch],
            font_size=7,
            repeat_rows=1,
        )
    )

    story.extend([PageBreak(), Paragraph("Complete portfolio day table", styles["Section"])])
    rows = [["Symbol", "Signal", "Price", "Prev close", "Move", "$/share", "Impact", "Value", "Source"]]
    for item in sorted(report.snapshots, key=lambda snapshot: snapshot.symbol):
        signal = "DEGRADED" if item.degraded else "UP" if item.move_pct > 0 else "DOWN" if item.move_pct < 0 else "FLAT"
        rows.append(
            [
                item.symbol,
                signal,
                f"${item.price:,.2f}",
                f"${item.previous_close:,.2f}",
                f"{item.move_pct:+.2f}%",
                f"${item.move_dollars:+,.2f}",
                f"${item.day_dollar_change:+,.2f}",
                f"${item.position_value:,.2f}",
                item.source,
            ]
        )
    story.append(
        _standard_table(
            rows,
            [
                0.55 * inch,
                0.72 * inch,
                0.75 * inch,
                0.78 * inch,
                0.62 * inch,
                0.68 * inch,
                0.82 * inch,
                0.82 * inch,
                0.72 * inch,
            ],
            font_size=6.8,
            repeat_rows=1,
        )
    )
    story.append(Spacer(1, 0.12 * inch))
    story.append(
        Paragraph(
            "Source and freshness: yFinance quote/history data is used for price moves. "
            "Production portfolio actions remain SEC/yFinance; multi-source evidence is labeled shadow context until activation.",
            styles["Muted"],
        )
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _signal_change_text(score: StockScore) -> str:
    state = str(score.metrics.get("signal_state", "new_coverage")).replace("_", " ")
    delta = score.metrics.get("score_delta")
    rank = score.metrics.get("rank_delta")
    if state == "steady" and delta == 0 and rank == 0:
        return "steady vs prior comparable run"
    pieces = [state]
    if isinstance(delta, (int, float)):
        pieces.append(f"score {delta:+.1f}")
    if isinstance(rank, int):
        pieces.append(f"rank {rank:+d}")
    return " | ".join(pieces)


def _compact_move(score: StockScore) -> str:
    delta = score.metrics.get("score_delta")
    rank = score.metrics.get("rank_delta")
    if delta == 0 and rank == 0:
        return "flat"
    if isinstance(delta, (int, float)):
        return f"{delta:+.1f} / {rank:+d}" if isinstance(rank, int) else f"{delta:+.1f}"
    return "new"


def _calibration_text(score: StockScore) -> str:
    sample_count = score.metrics.get("calibration_sample_count")
    confidence = str(score.metrics.get("calibration_confidence", "unmeasured"))
    horizon = score.metrics.get("calibration_horizon_days", 3)
    band = str(score.metrics.get("calibration_score_band", "unknown"))
    if not isinstance(sample_count, int) or sample_count <= 0:
        return f"{confidence}, {horizon}d {band}, n=0 episodes"
    win_rate = score.metrics.get("calibration_win_rate_pct")
    median = score.metrics.get("calibration_median_return_pct")
    pieces = [confidence, f"{horizon}d {band}", f"n={sample_count} episodes"]
    if isinstance(win_rate, (int, float)):
        pieces.append(f"win {win_rate:.0f}%")
    if isinstance(median, (int, float)):
        pieces.append(f"med {median:+.1f}%")
    return " | ".join(pieces)


def _eod_waterfall_chart(report: PortfolioEodReport) -> Drawing:
    drawing = Drawing(260, 165)
    drawing.add(String(18, 145, "Daily dollar result", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))
    baseline_x = 30
    y = 105
    max_value = max(
        abs(report.total_gain_dollars),
        abs(report.total_loss_dollars),
        abs(report.net_change_dollars),
        1.0,
    )
    bars = [
        ("Gains", report.total_gain_dollars, GREEN),
        ("Losses", report.total_loss_dollars, RED),
        ("Net", report.net_change_dollars, GREEN if report.net_change_dollars >= 0 else RED),
    ]
    for label, value, color in bars:
        width = min(150, abs(value) / max_value * 150)
        x = baseline_x if value >= 0 else baseline_x + 150 - width
        drawing.add(String(18, y + 4, label, fontName="Helvetica", fontSize=8, fillColor=MUTED))
        drawing.add(Rect(x, y, width, 12, fillColor=color, strokeColor=color))
        drawing.add(String(190, y + 3, f"${value:+,.0f}", fontName="Helvetica-Bold", fontSize=8, fillColor=INK))
        y -= 35
    drawing.add(String(baseline_x + 150, 40, "0", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    return drawing


def _eod_mover_chart(snapshots: list[PortfolioPriceSnapshot]) -> Drawing:
    visible = sorted(snapshots, key=lambda item: abs(item.move_pct), reverse=True)[:6]
    drawing = Drawing(260, 165)
    drawing.add(String(18, 145, "Largest % movers", fontName="Helvetica-Bold", fontSize=10, fillColor=INK))
    if not visible:
        drawing.add(String(25, 80, "No valid movers available", fillColor=MUTED))
        return drawing
    max_move = max(abs(item.move_pct) for item in visible) or 1.0
    y = 122
    for item in visible:
        color = GREEN if item.move_pct >= 0 else RED
        width = min(142, abs(item.move_pct) / max_move * 142)
        drawing.add(String(18, y + 2, item.symbol, fontName="Helvetica-Bold", fontSize=8, fillColor=INK))
        drawing.add(Rect(62, y, width, 10, fillColor=color, strokeColor=color))
        drawing.add(String(210, y + 1, f"{item.move_pct:+.1f}%", fontName="Helvetica", fontSize=8, fillColor=INK))
        y -= 20
    return drawing


def _snapshot_table(
    title: str,
    snapshots: list[PortfolioPriceSnapshot],
    styles,
) -> Table:
    rows = [[Paragraph(f"<b>{escape(title)}</b>", styles["BodySmall"]), "", ""]]
    rows.append(["Symbol", "Move", "Impact"])
    for item in snapshots:
        rows.append(
            [
                item.symbol,
                f"{item.move_pct:+.2f}%",
                f"${item.day_dollar_change:+,.0f}",
            ]
        )
    if len(rows) == 2:
        rows.append(["n/a", "n/a", "n/a"])
    table = Table(rows, colWidths=[0.9 * inch, 0.75 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("BACKGROUND", (0, 0), (-1, 0), WASH),
                ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _snapshot_row(item: PortfolioPriceSnapshot) -> list[str]:
    return [
        item.symbol,
        f"${item.price:,.2f}",
        f"{item.move_pct:+.2f}%",
        f"${item.move_dollars:+,.2f}",
        f"${item.day_dollar_change:+,.2f}",
        f"${item.position_value:,.2f}",
    ]


def _standard_table(
    rows: list[list[object]],
    widths: list[float],
    font_size: float = 7.2,
    repeat_rows: int = 1,
) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=repeat_rows)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("LEADING", (0, 0), (-1, -1), font_size + 2),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WASH]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def portfolio_pdf_filename(run_at: datetime) -> str:
    timezone_label = _filename_timezone(run_at)
    return f"portfolio-alert-{run_at.strftime('%Y-%m-%d-%H%M')}-{timezone_label}.pdf"


def universe_pdf_filename(run_at: datetime) -> str:
    timezone_label = _filename_timezone(run_at)
    return f"universe-alert-{run_at.strftime('%Y-%m-%d-%H%M')}-{timezone_label}.pdf"


def portfolio_pdf_caption(
    assessments: list[PortfolioAssessment],
    run_at: datetime,
) -> str:
    value = sum(item.current_value for item in assessments)
    priority = sum(
        item.action in {"EXIT REVIEW", "TRIM REVIEW", "BUY-MORE REVIEW"}
        for item in assessments
    )
    return (
        f"Portfolio Alert - {run_at.strftime('%b %d, %I:%M %p %Z')} | "
        f"${value:,.0f} value | {priority} priority review(s)"
    )


def universe_pdf_caption(scores: list[StockScore], run_at: datetime) -> str:
    candidates = [item for item in scores if item.is_alert]
    top = scores[0].symbol if scores else "none"
    return (
        f"Universe Alert - {run_at.strftime('%b %d, %I:%M %p %Z')} | "
        f"{len(candidates)} candidate(s) | top: {top}"
    )


def _filename_timezone(run_at: datetime) -> str:
    return "".join(
        character if character.isalnum() else "-"
        for character in (run_at.tzname() or "LOCAL")
    ).strip("-")
