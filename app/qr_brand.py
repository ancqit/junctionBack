"""In-memory Junction QR posters: brand mark, taglines, and PNG/PDF composition."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import qrcode
from qrcode.constants import ERROR_CORRECT_H

BRAND_NAME = "Junction"
JUNCTION_TODAY_URL = os.getenv("JUNCTION_TODAY_URL", "https://junction.today").rstrip("/")

FOREST = (25, 75, 49)
FOREST_DARK = (16, 48, 32)
GOLD = (243, 215, 130)
CREAM = (255, 252, 245)
INK = (36, 48, 42)
MUTED = (90, 104, 96)
WHITE = (255, 255, 255)

POSTER_W = 1080
POSTER_H = 1480


@dataclass(frozen=True)
class QrLine:
    id: str
    kind: str  # tagline, saying, or thought
    en: str
    hi: str


@dataclass(frozen=True)
class PosterArt:
    """Raster poster plus the clickable QR rectangle (top-left origin, pixels)."""

    png: bytes
    link_url: str
    qr_x: int
    qr_y: int
    qr_w: int
    qr_h: int


TAGLINES: tuple[QrLine, ...] = (
    QrLine(id="street-shop", kind="tagline", en="Your street. Your shop. One Junction.", hi="आपकी गली। आपकी दुकान। एक जंक्शन।"),
    QrLine(id="scan-shop-smile", kind="tagline", en="Scan. Shop. Smile.", hi="स्कैन करें। खरीदें। मुस्कुराएँ।"),
    QrLine(id="lane-alive", kind="tagline", en="Keep the lane alive — buy next door.", hi="गली जिलाए रखें — पड़ोस से खरीदें।"),
    QrLine(id="open-for-you", kind="tagline", en="Open for your Junction, every day.", hi="आपके जंक्शन के लिए, हर दिन खुला।"),
    QrLine(id="neighbours", kind="saying", en="Good neighbours make great markets.", hi="अच्छे पड़ोसी, बढ़िया बाज़ार।"),
    QrLine(id="small-kind", kind="saying", en="Small shops. Kind prices. Big welcome.", hi="छोटी दुकानें। सही दाम। बड़ा स्वागत।"),
    QrLine(id="share-table", kind="thought", en="A shared table starts with a shared street.", hi="साझा मेज़ की शुरुआत साझा गली से।"),
    QrLine(id="find-home", kind="thought", en="Find what you need, closer to home.", hi="जो चाहिए, घर के पास पाएँ।"),
    QrLine(id="one-scan", kind="tagline", en="One scan to your Junction.", hi="एक स्कैन, आपका जंक्शन।"),
    QrLine(id="come-through", kind="saying", en="Come through — the shop is expecting you.", hi="आइए — दुकान आपका इंतज़ार कर रही है।"),
    QrLine(id="galli-se", kind="saying", en="From your lane, for your lane.", hi="आपकी गली से, आपकी गली के लिए।"),
    QrLine(id="roz-ka-bazaar", kind="tagline", en="Your everyday bazaar, one scan away.", hi="रोज़ का बाज़ार, एक स्कैन दूर।"),
    QrLine(id="vishwas", kind="thought", en="Trust lives next door.", hi="भरोसा पड़ोस में रहता है।"),
    QrLine(id="ghar-ke-paas", kind="saying", en="Shop closer. Live easier.", hi="पास से खरीदें। आसान ज़िंदगी जिएँ।"),
    QrLine(id="apna-junction", kind="tagline", en="This is your Junction.", hi="यह आपका जंक्शन है।"),
    QrLine(id="muskaan", kind="saying", en="A smile at the counter beats a long delivery wait.", hi="काउंटर की मुस्कान, लंबी डिलीवरी से बेहतर।"),
)

TAGLINE_BY_ID = {line.id: line for line in TAGLINES}

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="Junction">
  <rect width="256" height="256" rx="85" fill="#f3d782"/>
  <text x="128" y="178" text-anchor="middle" font-family="Georgia, Times New Roman, serif" font-size="148" font-weight="700" fill="#194b31">J</text>
</svg>
"""


def resolve_taglines(
    ids: list[str] | None,
    *,
    fallback: str | None = None,
    custom_slogan: str | None = None,
) -> list[QrLine]:
    picked: list[QrLine] = []
    seen: set[str] = set()
    for raw in ids or []:
        key = (raw or "").strip()
        line = TAGLINE_BY_ID.get(key)
        if line and line.id not in seen:
            picked.append(line)
            seen.add(line.id)
        if len(picked) >= 3:
            break
    if not picked and fallback:
        line = TAGLINE_BY_ID.get(fallback.strip())
        if line:
            picked.append(line)
    custom = (custom_slogan or "").strip()
    if custom:
        # Custom slogan counts toward the three-line poster budget.
        if len(picked) >= 3:
            picked = picked[:2]
        picked.append(QrLine(id="custom", kind="custom", en=custom, hi=custom))
    if not picked:
        picked.append(TAGLINES[0])
    return picked


def build_junction_url(
    *,
    city: str,
    locality: str | None = None,
    shop_name: str | None = None,
    store_id: str | None = None,
) -> str:
    from urllib.parse import urlencode

    params: dict[str, str] = {"city": city.strip()}
    loc = (locality or "").strip()
    if loc:
        params["locality"] = loc
        params["marketplace"] = "1"
    name = (shop_name or "").strip()
    if name:
        params["shop"] = name
    sid = (store_id or "").strip()
    if sid:
        params["store_id"] = sid
    return f"{JUNCTION_TODAY_URL}/?{urlencode(params)}"


def _font_path(*candidates: str) -> str | None:
    for name in candidates:
        path = Path(name)
        if path.is_file():
            return str(path)
    return None


def _load_font(size: int, *, bold: bool = False, devanagari: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if devanagari:
        path = _font_path(
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Medium.ttf",
            "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
        )
        if path:
            return ImageFont.truetype(path, size)
    path = _font_path(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    )
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in text)


def draw_brand_mark(size: int = 160) -> Image.Image:
    """Gold rounded square with a forest J — the Front Web login mark."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(1, size // 40)
    draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=int(size * (12 / 36)), fill=GOLD)
    font = _load_font(int(size * 0.62), bold=True)
    letter = "J"
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1] - size * 0.02
    draw.text((x, y), letter, font=font, fill=FOREST)
    return img


def _qr_image(payload: str, box_size: int = 12) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=box_size,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.make_image(fill_color=FOREST_DARK, back_color=WHITE).convert("RGBA")
    mark = draw_brand_mark(size=max(48, matrix.size[0] // 5))
    # Quiet white pad so the logo stays scannable.
    pad = max(8, mark.size[0] // 10)
    badge = Image.new("RGBA", (mark.size[0] + pad * 2, mark.size[1] + pad * 2), WHITE + (255,))
    badge.paste(mark, (pad, pad), mark)
    bx = (matrix.size[0] - badge.size[0]) // 2
    by = (matrix.size[1] - badge.size[1]) // 2
    matrix.paste(badge, (bx, by), badge)
    return matrix


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if _text_width(draw, text, font) <= max_width:
        return [text]
    # Character wrap for Devanagari; word wrap otherwise.
    if _has_devanagari(text) or " " not in text:
        lines: list[str] = []
        current = ""
        for ch in text:
            trial = current + ch
            if _text_width(draw, trial, font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines or [text]
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def compose_poster(
    *,
    payload: str,
    junction_label: str,
    shop_name: str | None,
    lines: list[QrLine],
    lang: str = "en",
) -> bytes:
    """PNG bytes only (backward compatible)."""
    return compose_poster_art(
        payload=payload,
        junction_label=junction_label,
        shop_name=shop_name,
        lines=lines,
        lang=lang,
    ).png


def compose_poster_art(
    *,
    payload: str,
    junction_label: str,
    shop_name: str | None,
    lines: list[QrLine],
    lang: str = "en",
) -> PosterArt:
    use_hi = lang.lower().startswith("hi")
    poster = Image.new("RGB", (POSTER_W, POSTER_H), CREAM)
    draw = ImageDraw.Draw(poster)

    draw.rectangle((0, 0, POSTER_W, 132), fill=FOREST)
    draw.rectangle((0, POSTER_H - 120, POSTER_W, POSTER_H), fill=FOREST)

    mark = draw_brand_mark(76)
    poster.paste(mark, (48, 28), mark)

    title_font = _load_font(42, bold=True)
    draw.text((148, 42), BRAND_NAME, font=title_font, fill=GOLD)
    kicker_font = _load_font(18, bold=True)
    draw.text((148, 92), "Scan or tap for this Junction", font=kicker_font, fill=GOLD)

    y = 176
    if shop_name and shop_name.strip():
        shop = shop_name.strip()
        shop_font = _load_font(40, bold=True, devanagari=_has_devanagari(shop))
        for row in _wrap(draw, shop, shop_font, POSTER_W - 96):
            tw = _text_width(draw, row, shop_font)
            draw.text(((POSTER_W - tw) / 2, y), row, font=shop_font, fill=INK)
            y += 48
        y += 4

    place_font = _load_font(24, bold=True, devanagari=_has_devanagari(junction_label))
    for row in _wrap(draw, junction_label, place_font, POSTER_W - 96):
        tw = _text_width(draw, row, place_font)
        draw.text(((POSTER_W - tw) / 2, y), row, font=place_font, fill=MUTED)
        y += 32

    qr = _qr_image(payload, box_size=14)
    qr_size = 620
    qr = qr.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    card = Image.new("RGB", (qr_size + 48, qr_size + 48), WHITE)
    card_draw = ImageDraw.Draw(card)
    card_draw.rounded_rectangle((0, 0, card.size[0] - 1, card.size[1] - 1), radius=24, outline=GOLD, width=3)
    card.paste(qr.convert("RGB"), (24, 24))
    card_x = (POSTER_W - card.size[0]) // 2
    card_y = min(max(y + 36, 320), 400)
    poster.paste(card, (card_x, card_y))
    qr_x = card_x + 24
    qr_y = card_y + 24

    caption_y = card_y + card.size[1] + 40
    shown = lines[:2]
    for line in shown:
        text = line.hi if use_hi else line.en
        font = _load_font(24, bold=True, devanagari=_has_devanagari(text))
        wrapped = _wrap(draw, text, font, POSTER_W - 140)
        for row in wrapped:
            tw = _text_width(draw, row, font)
            draw.text(((POSTER_W - tw) / 2, caption_y), row, font=font, fill=FOREST)
            caption_y += 32
        caption_y += 6

    saying = (shown[0].hi if use_hi else shown[0].en) if shown else TAGLINES[0].en
    saying_font = _load_font(18, bold=True, devanagari=_has_devanagari(saying))
    saying_lines = _wrap(draw, saying, saying_font, POSTER_W - 120)
    foot_font = _load_font(20, bold=True)
    foot = JUNCTION_TODAY_URL.replace("https://", "").replace("http://", "")
    footer_y = POSTER_H - 88
    for row in saying_lines[:2]:
        tw = _text_width(draw, row, saying_font)
        draw.text(((POSTER_W - tw) / 2, footer_y), row, font=saying_font, fill=GOLD)
        footer_y += 24
    tw = _text_width(draw, foot, foot_font)
    draw.text(((POSTER_W - tw) / 2, POSTER_H - 42), foot, font=foot_font, fill=GOLD)

    buffer = io.BytesIO()
    poster.save(buffer, format="PNG", optimize=True)
    return PosterArt(
        png=buffer.getvalue(),
        link_url=payload,
        qr_x=qr_x,
        qr_y=qr_y,
        qr_w=qr_size,
        qr_h=qr_size,
    )


def compose_poster_pdf(art: PosterArt) -> bytes:
    """PDF with the poster image and a real hyperlink over the QR (PNG cannot do this)."""
    rgb = Image.open(io.BytesIO(art.png)).convert("RGB")
    jpeg_buf = io.BytesIO()
    rgb.save(jpeg_buf, format="JPEG", quality=92, optimize=True)
    jpeg = jpeg_buf.getvalue()
    return _pdf_with_linked_jpeg(
        jpeg=jpeg,
        width=POSTER_W,
        height=POSTER_H,
        link_url=art.link_url,
        # PDF y is from bottom-left.
        link_x=art.qr_x,
        link_y=POSTER_H - art.qr_y - art.qr_h,
        link_w=art.qr_w,
        link_h=art.qr_h,
    )


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_with_linked_jpeg(
    *,
    jpeg: bytes,
    width: int,
    height: int,
    link_url: str,
    link_x: int,
    link_y: int,
    link_w: int,
    link_h: int,
) -> bytes:
    """Minimal one-page PDF: full-page JPEG + URI link annotation over the QR."""
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    # 1 Catalog
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2 Pages
    add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    # 3 Page
    add(
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
        f"/Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> "
        f"/Annots [6 0 R] >>".encode("ascii")
    )
    # 4 Content stream — draw image
    content = f"q {width} 0 0 {height} 0 0 cm /Im1 Do Q\n".encode("ascii")
    add(f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream")
    # 5 Image XObject
    img_dict = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
        f"/Length {len(jpeg)} >>\nstream\n".encode("ascii")
        + jpeg
        + b"\nendstream"
    )
    add(img_dict)
    # 6 Link annotation (URI)
    annot = (
        f"<< /Type /Annot /Subtype /Link "
        f"/Rect [{link_x} {link_y} {link_x + link_w} {link_y + link_h}] "
        f"/Border [0 0 0] "
        f"/A << /S /URI /URI ({_pdf_escape(link_url)}) >> >>"
    ).encode("ascii")
    add(annot)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return out.getvalue()


def poster_filename(
    *,
    city: str,
    locality: str | None,
    shop_name: str | None,
    ext: str = "png",
) -> str:
    bits = ["junction"]
    for part in (shop_name, locality, city):
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (part or "").strip())
        cleaned = "-".join(filter(None, cleaned.split("-")))
        if cleaned:
            bits.append(cleaned)
    bits.append("qr")
    suffix = ext if ext.startswith(".") else f".{ext}"
    return "-".join(bits)[:80] + suffix
