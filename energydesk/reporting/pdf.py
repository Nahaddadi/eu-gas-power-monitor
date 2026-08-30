"""PDF export of the desk note.

Built with reportlab so the whole thing stays pure Python and renders
identically on a laptop and on Streamlit Cloud. The layout mirrors the
app's document: header, metric table, bottom-line callout, sections with
inline figures, risk bullets, analyst line, then the numbered sources.
Citation markers [n] become superscript links to the source URL.
"""

import io

from reportlab.lib import colors

from energydesk.config import (
    DEFAULT_EFFICIENCY, DEFAULT_EMISSION_FACTOR, DEFAULT_VARIABLE_OPEX,
)
from energydesk.reporting.citations import cited_pdf, pdf_escape, pdf_safe
from energydesk.reporting.noteblocks import PENDING
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)
from reportlab.platypus.flowables import Flowable

NAVY = colors.HexColor("#1f3a68")
GREY = colors.HexColor("#6b7280")
LIGHT = colors.HexColor("#f1f3f7")
AMBER_BG = colors.HexColor("#fdf6ea")
AMBER_BAR = colors.HexColor("#d99a2b")

class Figure(Flowable):
    """Chart plus caption as one unsplittable block.

    Instead of jumping to the next page when it does not fit, the figure
    first shrinks to the space left on the current page; only when that
    would make it uselessly small does it move whole to the next page.
    This keeps pages evenly filled.
    """

    def __init__(self, path: str, caption: str, style: ParagraphStyle,
                 max_width: float, max_height: float,
                 min_height: float = 40 * mm):
        super().__init__()
        self.path = path
        self.caption = pdf_safe(caption)
        self.style = style
        self.max_width = max_width
        self.max_height = max_height
        self.min_height = min_height
        self._img = ImageReader(path)
        self.iw, self.ih = self._img.getSize()

    def _caption_block(self, width: float) -> list[str]:
        return simpleSplit(self.caption, self.style.fontName,
                           self.style.fontSize, width)

    def wrap(self, availWidth: float, availHeight: float):
        width = min(self.max_width, availWidth)
        height = self.ih * (width / self.iw)
        cap_lines = self._caption_block(width)
        gap = 5
        cap_h = len(cap_lines) * self.style.leading + gap

        limit = min(self.max_height, availHeight - cap_h - 2)
        moved = False
        if height > limit:
            if limit >= self.min_height:
                height = limit          # shrink to fit the current page
            else:
                moved = True            # render full size on a fresh page

        self._draw_w, self._draw_h = width, height
        self._cap_lines, self._gap = cap_lines, gap
        total = height + cap_h + 2
        if moved:
            # taller than the remaining space so the frame defers us
            total = availHeight + 1
        self.width, self.height = width, total
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        cap_h = len(self._cap_lines) * self.style.leading + self._gap
        x = (self.width - self._draw_w) / 2
        y = cap_h
        canvas.drawImage(self._img, x, y, self._draw_w, self._draw_h,
                         preserveAspectRatio=True, mask="auto")
        canvas.setFont(self.style.fontName, self.style.fontSize)
        canvas.setFillColor(self.style.textColor)
        ty = y - self.style.leading + 3
        for line in self._cap_lines:
            tx = (self.width - stringWidth(line, self.style.fontName,
                                           self.style.fontSize)) / 2
            canvas.drawString(tx, ty, line)
            ty -= self.style.leading


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=17, leading=21,
            textColor=NAVY, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Helvetica", fontSize=10, leading=13,
            textColor=GREY, spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=GREY,
        ),
        "section": ParagraphStyle(
            "section", fontName="Helvetica-Bold", fontSize=12.5, leading=15,
            textColor=NAVY, spaceBefore=22, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=9.5, leading=14,
            spaceAfter=8,
        ),
        "callout": ParagraphStyle(
            "callout", fontName="Helvetica-Bold", fontSize=9.5, leading=14,
        ),
        "finding": ParagraphStyle(
            "finding", fontName="Helvetica", fontSize=9.5, leading=13.5,
        ),
        "analyst": ParagraphStyle(
            "analyst", fontName="Helvetica-Oblique", fontSize=9, leading=12,
            textColor=GREY, spaceBefore=14,
        ),
        "caption": ParagraphStyle(
            "caption", fontName="Helvetica-Oblique", fontSize=8, leading=10,
            textColor=GREY, alignment=TA_CENTER, spaceBefore=2, spaceAfter=10,
        ),
        "cell": ParagraphStyle(
            "cell", fontName="Helvetica", fontSize=8, leading=10,
        ),
        "cellhead": ParagraphStyle(
            "cellhead", fontName="Helvetica-Bold", fontSize=8, leading=10,
            textColor=colors.white,
        ),
    }


def note_pdf(blocks, table_rows: list[dict], date_label: str,
             sources: list[dict], author: str = "") -> bytes:
    """Render the block list as a PDF, returning its bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"European Energy Desk Note - {date_label}",
        author="EU Gas / Power Desk Monitor",
    )
    st = _styles()
    flow: list = []

    flow.append(Paragraph("European Energy Desk Note", st["title"]))
    flow.append(Paragraph("German Power | TTF Gas | EUA Carbon", st["subtitle"]))
    header_meta = f"{date_label}" + (f" - {author}" if author else "")
    flow.append(Paragraph(header_meta, st["meta"]))
    flow.append(Spacer(1, 2))
    flow.append(HRFlowable(width="100%", thickness=1.6, color=NAVY,
                           spaceBefore=2, spaceAfter=8))

    pending = sum(1 for b in blocks if b.reviewable and b.status == PENDING)
    if pending:
        flow.append(Paragraph(
            f"Draft status: {pending} block(s) still marked 'needs review'.",
            st["meta"]))

    if table_rows:
        header = list(table_rows[0].keys())
        data = [[Paragraph(pdf_escape(str(h)).upper(), st["cellhead"])
                 for h in header]]
        for row in table_rows:
            data.append([Paragraph(pdf_escape(str(row.get(h, ""))), st["cell"])
                         for h in header])
        table = Table(data, colWidths=[doc.width / len(header)] * len(header),
                      repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8dde5")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        table.spaceAfter = 14
        flow.append(table)

    for block in blocks:
        kind = block.kind
        text = block.display_text

        if kind == "heading":
            flow.append(Paragraph(pdf_escape(str(block.content)), st["section"]))
            flow.append(HRFlowable(width=64, thickness=2, color=AMBER_BAR,
                                   hAlign="LEFT", spaceBefore=1,
                                   spaceAfter=8))
        elif kind == "callout":
            box = Table(
                [[Paragraph(cited_pdf(text, sources), st["callout"])]],
                colWidths=[doc.width],
            )
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("LINEBEFORE", (0, 0), (0, -1), 3, NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            box.spaceBefore = 4
            box.spaceAfter = 12
            flow.append(box)
        elif kind == "prose":
            flow.append(Paragraph(cited_pdf(text, sources), st["body"]))
        elif kind == "finding":
            bullet_box = Table(
                [[Paragraph("&bull; " + cited_pdf(text, sources),
                            st["finding"])]],
                colWidths=[doc.width],
            )
            bullet_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, AMBER_BAR),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            bullet_box.spaceAfter = 6
            flow.append(bullet_box)
        elif kind == "chart":
            path, caption = block.content
            try:
                flow.append(Figure(str(path), caption, st["caption"],
                                   max_width=min(doc.width, 150 * mm),
                                   max_height=doc.height * 0.5))
            except OSError:
                continue
        elif kind == "meta":
            flow.append(Paragraph(cited_pdf(text, sources), st["analyst"]))

    # Closing section: plant conventions first, then data sources, then
    # the research references.
    assumptions = (
        f"Assumptions: gas plant efficiency {DEFAULT_EFFICIENCY:.0%}, "
        f"emissions factor {DEFAULT_EMISSION_FACTOR} tCO2/MWh, variable "
        f"O&amp;M EUR {DEFAULT_VARIABLE_OPEX:.0f}/MWh. Forward German power "
        "prices are not publicly quoted; the TTF curve shape is used as a "
        "directional proxy only."
    )
    data_lines = [
        "GIE AGSI+ - EU underground gas storage",
        "ICE Endex TTF via Yahoo Finance - front and deferred gas contracts",
        "Energy-Charts (Fraunhofer ISE) - German day-ahead power prices",
        "EEX - EUA carbon spot",
    ]
    closing: list = [Paragraph("Assumptions and Sources", st["section"]),
                     HRFlowable(width=64, thickness=2, color=AMBER_BAR,
                                hAlign="LEFT", spaceBefore=1, spaceAfter=8),
                     Paragraph(assumptions, st["cell"])]
    closing.append(Spacer(1, 3))
    for line in data_lines:
        closing.append(Paragraph("&bull; " + pdf_escape(line), st["cell"]))
        closing.append(Spacer(1, 1))
    # The conventions block reads as one unit; never split it across pages.
    flow.append(KeepTogether(closing))

    if sources:
        flow.append(Spacer(1, 10))
        flow.append(Paragraph("Research references", st["section"]))
        for i, src in enumerate(sources, start=1):
            title = pdf_escape(src.get("title") or src["url"])
            url = src["url"].replace('"', "%22")
            entry = Paragraph(
                f'{i}. <a href="{url}" color="#1f3a68">{title}</a><br/>'
                f'<font size="7.5" color="#6b7280">{pdf_escape(url)}</font>',
                st["cell"],
            )
            flow.append(KeepTogether([entry, Spacer(1, 2)]))

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(18 * mm, 10 * mm, "Generated by EU Gas / Power "
                          "Desk Monitor - draft for review, not investment "
                          "advice")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm,
                               f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
