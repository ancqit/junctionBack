"""jEarth Home trash PDF: the customer's bucket grouped by waste stream, with hand-over steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .junction_pdf import RULE, JunctionDocument
from .qr_brand import FOREST, INK, MUTED, WHITE

IST = timezone(timedelta(hours=5, minutes=30), "IST")
HOME_TRASH_URL = "https://www.junction.earth/home-trash"
HOST = "junction.earth"

STREAM_ORDER = (
    "wet",
    "dry",
    "sanitary",
    "ewaste",
    "hazardous",
    "construction",
    "reject",
)
STREAM_LABELS: dict[str, tuple[str, str]] = {
    "wet": ("Wet / organic", "गीला / जैविक"),
    "dry": ("Dry / recyclable", "सूखा / रीसायकल"),
    "sanitary": ("Sanitary", "सैनिटरी"),
    "ewaste": ("E-waste", "ई-वेस्ट"),
    "hazardous": ("Hazardous", "खतरनाक"),
    "construction": ("Construction & demolition", "निर्माण मलबा"),
    "reject": ("Reject / residual", "रिजेक्ट / अवशेष"),
}
STREAM_COLORS: dict[str, tuple[int, int, int]] = {
    "wet": (46, 125, 50),
    "dry": (21, 101, 192),
    "sanitary": (173, 20, 87),
    "ewaste": (94, 53, 177),
    "hazardous": (198, 40, 40),
    "construction": (121, 85, 72),
    "reject": (84, 84, 84),
}

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Home trash",
        "subtitle": "What is in this home's trash, and how to hand each item over.",
        "household": "Household",
        "phone": "Phone",
        "date": "Date",
        "items": "Items",
        "count": "{items} items in {streams} streams",
        "count_one": "1 item",
        "how": "How to hand it over",
        "source": "Source",
        "empty": "Your Home trash is empty. Search an item on junction.earth and add it here.",
        "closing": "Hand each stream to its own collector. Your city's rules come first.",
        "customer": "Junction customer",
        "scan": "Scan to open your Home trash on jEarth",
        "slogan": "Sort at home. Hand it over right. One Junction.",
        "ref": "Home trash",
    },
    "hi": {
        "title": "घर का कचरा",
        "subtitle": "इस घर के कचरे में क्या है, और हर चीज़ कैसे सौंपनी है।",
        "household": "घर",
        "phone": "फ़ोन",
        "date": "तारीख",
        "items": "चीज़ें",
        "count": "{streams} प्रकार में {items} चीज़ें",
        "count_one": "1 चीज़",
        "how": "कैसे सौंपें",
        "source": "स्रोत",
        "empty": "आपका घर का कचरा खाली है। junction.earth पर कोई चीज़ खोजें और यहाँ जोड़ें।",
        "closing": "हर प्रकार का कचरा उसके अपने संग्रहकर्ता को दें। शहर के नियम पहले।",
        "customer": "जंक्शन ग्राहक",
        "scan": "jEarth पर अपना घर का कचरा खोलने के लिए स्कैन करें",
        "slogan": "घर पर छाँटें। सही हाथ में सौंपें। एक जंक्शन।",
        "ref": "घर का कचरा",
    },
}


@dataclass
class TrashItem:
    title_en: str
    title_hi: str
    stream: str
    dispose_en: list[str] = field(default_factory=list)
    dispose_hi: list[str] = field(default_factory=list)
    source_url: str = ""


@dataclass
class HomeTrashInput:
    display_name: str | None
    phone_number: str
    created_at: datetime
    items: list[TrashItem]


def mask_phone(phone: str) -> str:
    digits = phone.strip()
    if len(digits) < 8:
        return digits
    return f"{digits[:5]}••••{digits[-4:]}"


def _stream(value: str) -> str:
    return value if value in STREAM_LABELS else "reject"


def home_trash_filename(created_at: datetime) -> str:
    return f"junction-home-trash-{created_at.astimezone(IST):%Y%m%d}.pdf"


class _HomeTrashDocument(JunctionDocument):
    def __init__(self, data: HomeTrashInput, lang: str) -> None:
        self.data = data
        self.labels = LABELS[lang]
        super().__init__(
            lang=lang,
            qr_url=HOME_TRASH_URL,
            host=HOST,
            slogan=self.labels["slogan"],
            scan_label=self.labels["scan"],
            header_ref=self.labels["ref"],
            title=LABELS["en"]["title"],
            author=data.display_name or LABELS["en"]["customer"],
        )

    def _label(self, stream: str) -> str:
        en, hi = STREAM_LABELS[_stream(stream)]
        return hi if self.lang == "hi" else en

    def _title(self, item: TrashItem) -> tuple[str, str]:
        primary = item.title_hi if self.lang == "hi" else item.title_en
        other = item.title_en if self.lang == "hi" else item.title_hi
        primary = primary or other
        return primary, (other if other and other != primary else "")

    def _title_block(self) -> None:
        self._font(24, "B", FOREST)
        self.cell(0, 28, self.labels["title"], new_x="LMARGIN", new_y="NEXT")
        self._font(10, "", MUTED)
        self.multi_cell(
            0, 14, self.labels["subtitle"], align="L", new_x="LMARGIN", new_y="NEXT"
        )

    def _meta_rows(self) -> None:
        data = self.data
        when = data.created_at.astimezone(IST).strftime(
            "%d/%m/%Y, %I:%M %p IST" if self.lang == "hi" else "%d %b %Y, %I:%M %p IST"
        )
        streams = {_stream(item.stream) for item in data.items}
        if not data.items:
            count = "0"
        elif len(data.items) == 1:
            count = self.labels["count_one"]
        else:
            count = self.labels["count"].format(
                items=len(data.items), streams=len(streams)
            )
        rows = [
            (self.labels["household"], data.display_name or self.labels["customer"]),
            (self.labels["phone"], mask_phone(data.phone_number)),
            (self.labels["date"], when),
            (self.labels["items"], count),
        ]
        label_w = 90
        for label, value in rows:
            self._font(9, "", MUTED)
            self.cell(label_w, 16, label)
            self._font(10, "B", INK)
            self.cell(0, 16, value, new_x="LMARGIN", new_y="NEXT")

    def _summary_chips(self, groups: list[tuple[str, list[TrashItem]]]) -> None:
        x = self.l_margin
        y = self.get_y()
        chip_h = 20
        for stream, items in groups:
            text = f"{self._label(stream)}  {len(items)}"
            self._font(9, "B", WHITE)
            width = self.get_string_width(text) + 20
            if x + width > self.w - self.r_margin:
                x = self.l_margin
                y += chip_h + 6
            self.set_fill_color(*STREAM_COLORS[stream])
            self.rect(
                x,
                y,
                width,
                chip_h,
                style="F",
                round_corners=True,
                corner_radius=chip_h / 2,
            )
            self._font(9, "B", WHITE)
            self.set_xy(x, y)
            self.cell(width, chip_h, text, align="C")
            x += width + 6
        self.set_xy(self.l_margin, y + chip_h + 4)

    def _stream_heading(self, stream: str, count: int, first_item_h: float) -> None:
        self._ensure_room(34 + first_item_h)
        y = self.get_y() + 6
        color = STREAM_COLORS[stream]
        self.set_fill_color(*color)
        self.rect(self.l_margin, y, 5, 22, style="F")
        self._font(13, "B", color)
        self.set_xy(self.l_margin + 14, y)
        self.cell(self.content_w - 14, 22, f"{self._label(stream)}  ·  {count}")
        self.set_xy(self.l_margin, y + 28)

    def _item_lines(self, width: float, text: str, size: float, style: str = "") -> int:
        self._font(size, style)
        return len(self.multi_cell(width, size + 3, text, dry_run=True, output="LINES"))

    def _steps(self, item: TrashItem) -> list[str]:
        steps = (
            (item.dispose_hi if self.lang == "hi" else item.dispose_en)
            or item.dispose_en
            or item.dispose_hi
        )
        # The bundled fonts have no arrow glyph; seed advice uses "→" for "then".
        return [step.replace("→", "—") for step in steps]

    def _item_height(self, item: TrashItem) -> float:
        text_w = self.w - self.r_margin - (self.l_margin + 26)
        height = 18 + (14 if self._title(item)[1] else 0) + 14
        height += sum(
            self._item_lines(text_w - 14, step, 9.5) * 12.5
            for step in self._steps(item)
        )
        return height + (14 if item.source_url else 0) + 10

    def _item(self, item: TrashItem) -> None:
        primary, other = self._title(item)
        steps = self._steps(item)
        text_x = self.l_margin + 26
        text_w = self.w - self.r_margin - text_x
        self._ensure_room(self._item_height(item))

        top = self.get_y()
        self.set_draw_color(*FOREST)
        self.set_line_width(1)
        self.rect(
            self.l_margin + 4, top + 3, 12, 12, round_corners=True, corner_radius=2
        )
        self._font(12, "B", INK)
        self.set_xy(text_x, top)
        self.multi_cell(text_w, 18, primary, align="L", new_x="LEFT", new_y="NEXT")
        if other:
            self._font(9, "", MUTED)
            self.set_x(text_x)
            self.cell(text_w, 14, other, new_x="LEFT", new_y="NEXT")
        if steps:
            self._font(8.5, "B", FOREST)
            self.set_x(text_x)
            self.cell(text_w, 14, self.labels["how"], new_x="LEFT", new_y="NEXT")
            for index, step in enumerate(steps, start=1):
                self._font(9.5, "", INK)
                self.set_x(text_x)
                self.cell(14, 12.5, f"{index}.")
                self._font(9.5, "", INK)
                self.multi_cell(
                    text_w - 14, 12.5, step, align="L", new_x="LEFT", new_y="NEXT"
                )
                self.set_x(text_x)
        if item.source_url:
            host = urlparse(item.source_url).netloc or item.source_url
            self._font(8, "", MUTED)
            self.set_x(text_x)
            self.cell(
                text_w,
                14,
                f"{self.labels['source']}: {host}",
                link=item.source_url,
                new_x="LMARGIN",
                new_y="NEXT",
            )
        bottom = self.get_y() + 6
        self.set_draw_color(*RULE)
        self.set_line_width(0.5)
        self.line(text_x, bottom, self.w - self.r_margin, bottom)
        self.set_xy(self.l_margin, bottom + 8)

    def _groups(self) -> list[tuple[str, list[TrashItem]]]:
        by_stream: dict[str, list[TrashItem]] = {}
        for item in self.data.items:
            by_stream.setdefault(_stream(item.stream), []).append(item)
        return [
            (stream, by_stream[stream])
            for stream in STREAM_ORDER
            if stream in by_stream
        ]

    def render(self) -> bytes:
        self.add_page()
        self._title_block()
        self._divider()
        self._meta_rows()
        self._divider(6)
        groups = self._groups()
        if not groups:
            self._font(11, "", MUTED)
            self.multi_cell(
                0, 16, self.labels["empty"], align="L", new_x="LMARGIN", new_y="NEXT"
            )
        else:
            self._summary_chips(groups)
            for stream, items in groups:
                self._stream_heading(stream, len(items), self._item_height(items[0]))
                for item in items:
                    self._item(item)
            self._ensure_room(30)
            self._font(9, "", MUTED)
            self.multi_cell(
                0, 14, self.labels["closing"], align="L", new_x="LMARGIN", new_y="NEXT"
            )
        return bytes(self.output())


def render_home_trash(data: HomeTrashInput, lang: str = "en") -> bytes:
    return _HomeTrashDocument(data, "hi" if lang == "hi" else "en").render()
