"""Shared Junction PDF chrome: forest header band with the J mark, branded QR footer, Hindi-safe fonts."""

from __future__ import annotations

import io

from fpdf import FPDF

from .qr_brand import BUNDLED_FONT_DIR as FONT_DIR
from .qr_brand import FOREST, GOLD, INK, WHITE, qr_image

PAPER = (244, 240, 230)
GOLD_SOFT = (247, 233, 184)
RULE = (200, 196, 184)

PAGE_MARGIN = 48
HEADER_H = 72
FOOTER_H = 104
QR_SIZE = 76


class JunctionDocument(FPDF):
    """A4 portrait page with Junction header and QR footer; subclasses draw the body."""

    def __init__(
        self,
        *,
        lang: str,
        qr_url: str,
        host: str,
        slogan: str,
        scan_label: str,
        header_ref: str,
        title: str,
        author: str,
    ) -> None:
        super().__init__(orientation="portrait", unit="pt", format="A4")
        self.lang = lang
        self.qr_url = qr_url
        self.host = host
        self.slogan = slogan
        self.scan_label = scan_label
        self.header_ref = header_ref
        qr_png = io.BytesIO()
        qr_image(qr_url).convert("RGB").save(qr_png, format="PNG")
        self.qr_png = qr_png.getvalue()

        self.add_font("sans", "", FONT_DIR / "NotoSans-Regular.ttf")
        self.add_font("sans", "B", FONT_DIR / "NotoSans-Bold.ttf")
        self.add_font("serif", "I", FONT_DIR / "NotoSerif-Italic.ttf")
        self.add_font("deva", "", FONT_DIR / "NotoSansDevanagari-Regular.ttf")
        self.add_font("deva", "B", FONT_DIR / "NotoSansDevanagari-Bold.ttf")
        self.set_fallback_fonts(["deva"], exact_match=False)
        self.set_text_shaping(True)

        self.set_margins(PAGE_MARGIN, HEADER_H + 28, PAGE_MARGIN)
        self.set_auto_page_break(auto=True, margin=FOOTER_H + 16)
        self.alias_nb_pages()
        self.set_title(title)
        self.set_author(author)
        self.set_creator(host)

    @property
    def content_w(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def _font(
        self,
        size: float,
        style: str = "",
        color: tuple[int, int, int] = INK,
        family: str = "sans",
    ) -> None:
        # fpdf2 skips re-selecting an unchanged font even after a Devanagari fallback
        # fragment switched the stream font, which garbles the next Latin run.
        self.set_font("deva" if family != "deva" else "sans", "", size)
        self.set_font(family, style, size)
        self.set_text_color(*color)

    def _mark(self, x: float, y: float, size: float) -> None:
        self.set_fill_color(*GOLD)
        self.rect(
            x, y, size, size, style="F", round_corners=True, corner_radius=size / 3
        )
        self._font(size * 0.62, "B", FOREST)
        self.set_xy(x, y)
        self.cell(size, size, "J", align="C")

    def header(self) -> None:
        self.set_fill_color(*PAPER)
        self.rect(0, 0, self.w, self.h, style="F")
        self.set_fill_color(*FOREST)
        self.rect(0, 0, self.w, HEADER_H, style="F")
        self._mark(PAGE_MARGIN, 16, 40)
        self._font(20, "B", GOLD)
        self.set_xy(PAGE_MARGIN + 52, 16)
        self.cell(200, 22, "Junction")
        self._font(10, "", GOLD)
        self.set_xy(PAGE_MARGIN + 52, 38)
        self.cell(200, 16, self.host)
        self._font(10, "B", GOLD)
        self.set_xy(self.w - PAGE_MARGIN - 240, 22)
        self.cell(240, 14, self.header_ref, align="R")
        self._font(9, "", GOLD)
        self.set_xy(self.w - PAGE_MARGIN - 240, 38)
        self.cell(240, 14, f"{self.page_no()}/{{nb}}", align="R")
        self.set_xy(self.l_margin, self.t_margin)

    def footer(self) -> None:
        top = self.h - FOOTER_H
        self.set_fill_color(*FOREST)
        self.rect(0, top, self.w, FOOTER_H, style="F")
        qr_y = top + (FOOTER_H - QR_SIZE) / 2
        self.set_fill_color(*WHITE)
        self.rect(
            PAGE_MARGIN - 4,
            qr_y - 4,
            QR_SIZE + 8,
            QR_SIZE + 8,
            style="F",
            round_corners=True,
            corner_radius=6,
        )
        self.image(
            io.BytesIO(self.qr_png),
            PAGE_MARGIN,
            qr_y,
            QR_SIZE,
            QR_SIZE,
            link=self.qr_url,
        )
        text_x = PAGE_MARGIN + QR_SIZE + 20
        self._font(12, "I", GOLD, family="serif")
        self.set_xy(text_x, qr_y + 10)
        self.cell(self.w - text_x - PAGE_MARGIN, 18, self.slogan)
        self._font(10, "", GOLD)
        self.set_xy(text_x, qr_y + 34)
        self.cell(self.w - text_x - PAGE_MARGIN, 14, self.host)
        self._font(8, "", GOLD)
        self.set_xy(text_x, qr_y + 52)
        self.cell(self.w - text_x - PAGE_MARGIN, 12, self.scan_label)

    def _divider(self, gap: float = 10) -> None:
        y = self.get_y() + gap
        self.set_draw_color(*FOREST)
        self.set_line_width(0.9)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.set_y(y + gap + 4)

    def _ensure_room(self, height: float) -> bool:
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()
            return True
        return False
