"""Junction order bill PDF: one layout for junction.today, the shop back-office and junction.earth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .junction_pdf import GOLD_SOFT, RULE, JunctionDocument
from .qr_brand import (
    FOREST,
    GOLD,
    INK,
    JUNCTION_TODAY_URL,
    MUTED,
    WHITE,
    build_junction_url,
)

IST = timezone(timedelta(hours=5, minutes=30), "IST")

SIGNATURE_H = 96

DEFAULT_SLOGAN = {
    "en": "Your street. Your shop. One Junction.",
    "hi": "आपकी गली। आपकी दुकान। एक जंक्शन।",
}

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Order bill",
        "bill_no": "Bill no.",
        "date": "Date",
        "billed_to": "Billed to",
        "phone": "Phone",
        "payment": "Payment",
        "status": "Status",
        "item": "Item",
        "qty": "Qty",
        "rate": "Rate",
        "amount": "Amount",
        "subtotal": "Subtotal",
        "tax": "GST",
        "discount": "Discount",
        "shipping": "Delivery",
        "total": "Total",
        "total_paid": "Paid in full",
        "total_due": "Amount due at the shop",
        "notes": "Notes",
        "customer_sign": "Customer signature",
        "shop_sign": "Shop signature",
        "name": "Name",
        "sign_date": "Date",
        "scan": "Scan for this Junction",
        "gstin": "GSTIN",
        "neighbour": "Neighbour",
    },
    "hi": {
        "title": "ऑर्डर बिल",
        "bill_no": "बिल नंबर",
        "date": "तारीख",
        "billed_to": "ग्राहक",
        "phone": "फ़ोन",
        "payment": "भुगतान",
        "status": "स्थिति",
        "item": "सामान",
        "qty": "मात्रा",
        "rate": "दर",
        "amount": "राशि",
        "subtotal": "उप-योग",
        "tax": "जीएसटी",
        "discount": "छूट",
        "shipping": "डिलीवरी",
        "total": "कुल",
        "total_paid": "पूरा भुगतान हो गया",
        "total_due": "दुकान पर देय राशि",
        "notes": "टिप्पणी",
        "customer_sign": "ग्राहक के हस्ताक्षर",
        "shop_sign": "दुकान के हस्ताक्षर",
        "name": "नाम",
        "sign_date": "तारीख",
        "scan": "इस जंक्शन के लिए स्कैन करें",
        "gstin": "जीएसटीआईएन",
        "neighbour": "पड़ोसी",
    },
}

PAYMENT_METHODS = {
    "cash": ("Cash", "नकद"),
    "card": ("Card", "कार्ड"),
    "upi": ("UPI", "यूपीआई"),
    "bank_transfer": ("Bank transfer", "बैंक ट्रांसफ़र"),
    "other": ("Other", "अन्य"),
}
PAYMENT_STATUSES = {
    "pending": ("Not paid yet", "अभी भुगतान नहीं"),
    "paid": ("Paid", "भुगतान हो गया"),
    "failed": ("Failed", "विफल"),
    "refunded": ("Refunded", "वापस किया गया"),
}
ORDER_STATUSES = {
    "pending": ("Pending", "लंबित"),
    "confirmed": ("Confirmed", "पक्का"),
    "completed": ("Completed", "पूरा"),
    "cancelled": ("Cancelled", "रद्द"),
}


@dataclass(frozen=True)
class BillLine:
    name: str
    quantity: int
    unit_price: float


@dataclass(frozen=True)
class BillInput:
    order_number: str
    created_at: datetime
    customer_name: str
    customer_phone: str | None
    lines: list[BillLine]
    subtotal: float
    tax: float
    discount: float
    shipping: float
    total: float
    currency: str
    payment_method: str
    payment_status: str
    status: str
    notes: str | None
    shop_name: str
    shop_address: str | None
    gstin: str | None
    qr_url: str
    host: str


def _pick(table: dict[str, tuple[str, str]], key: str, lang: str) -> str:
    pair = table.get((key or "").strip().lower())
    if pair is None:
        return (key or "").strip() or "-"
    return pair[1] if lang == "hi" else pair[0]


def _group_indian(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    whole, fraction = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}{','.join([*groups, tail])}.{fraction}"


def format_money(amount: float, currency: str) -> str:
    code = (currency or "INR").strip().upper() or "INR"
    grouped = _group_indian(float(amount or 0))
    return f"₹ {grouped}" if code == "INR" else f"{code} {grouped}"


def gst_label(label: str, tax: float, subtotal: float) -> str:
    if subtotal <= 0:
        return label
    return f"{label} ({round(tax / subtotal * 100, 2):g}%)"


def _host_for_source(source: str | None) -> str:
    value = (source or "").strip().lower()
    if value.startswith("junction.earth"):
        return "junction.earth"
    return JUNCTION_TODAY_URL.replace("https://", "").replace("http://", "")


def bill_input_from_order(order: dict, shop: dict | None) -> BillInput:
    billing = order.get("billing") or {}
    shop = shop or {}
    city = str(shop.get("city") or "").strip()
    locality = str(shop.get("locality") or "").strip()
    shop_name = str(shop.get("name") or "").strip() or "Junction shop"
    address = ", ".join(
        part for part in (str(shop.get("address") or "").strip(), locality, city) if part
    )
    qr_url = (
        build_junction_url(
            city=city,
            locality=locality or None,
            shop_name=shop_name,
            store_id=str(order.get("store_id") or ""),
        )
        if city
        else JUNCTION_TODAY_URL
    )
    created_at = order.get("created_at") or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return BillInput(
        order_number=str(order.get("order_number") or ""),
        created_at=created_at,
        customer_name=str(order.get("customer_name") or "").strip(),
        customer_phone=order.get("customer_phone"),
        lines=[
            BillLine(
                name=str(item.get("product_name") or "").strip() or "-",
                quantity=int(item.get("quantity") or 0),
                unit_price=float(item.get("unit_price") or 0),
            )
            for item in order.get("items") or []
        ],
        subtotal=float(billing.get("subtotal") or 0),
        tax=float(billing.get("tax_amount") or 0),
        discount=float(billing.get("discount_amount") or 0),
        shipping=float(billing.get("shipping_amount") or 0),
        total=float(billing.get("total_amount") or 0),
        currency=str(billing.get("currency") or "INR"),
        payment_method=str(billing.get("payment_method") or ""),
        payment_status=str(billing.get("payment_status") or "pending"),
        status=str(order.get("status") or "pending"),
        notes=(order.get("notes") or "").strip() or None,
        shop_name=shop_name,
        shop_address=address or None,
        gstin=(str(shop.get("gstin") or "").strip() or None) if shop.get("gst_verified") else None,
        qr_url=qr_url,
        host=_host_for_source(order.get("source")),
    )


def bill_filename(order_number: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in order_number.strip()) or "order"
    return f"junction-bill-{safe}.pdf"


class _BillDocument(JunctionDocument):
    def __init__(self, bill: BillInput, lang: str) -> None:
        self.bill = bill
        self.labels = LABELS[lang]
        super().__init__(
            lang=lang,
            qr_url=bill.qr_url,
            host=bill.host,
            slogan=DEFAULT_SLOGAN[lang],
            scan_label=self.labels["scan"],
            header_ref=f"{self.labels['bill_no']}  {bill.order_number}",
            title=f"{LABELS['en']['title']} {bill.order_number}",
            author=bill.shop_name,
        )

    def _title_block(self) -> None:
        self._font(24, "B", FOREST)
        self.cell(0, 28, self.labels["title"], new_x="LMARGIN", new_y="NEXT")
        self.ln(8)
        self._font(15, "B", INK)
        self.multi_cell(0, 19, self.bill.shop_name, align="L", new_x="LMARGIN", new_y="NEXT")
        if self.bill.shop_address:
            self._font(10, "", MUTED)
            self.multi_cell(0, 14, self.bill.shop_address, align="L", new_x="LMARGIN", new_y="NEXT")
        if self.bill.gstin:
            self._font(9, "", MUTED)
            self.cell(0, 14, f"{self.labels['gstin']}: {self.bill.gstin}", new_x="LMARGIN", new_y="NEXT")

    def _meta_rows(self) -> None:
        bill = self.bill
        when = bill.created_at.astimezone(IST).strftime(
            "%d/%m/%Y, %I:%M %p IST" if self.lang == "hi" else "%d %b %Y, %I:%M %p IST"
        )
        payment = (
            f"{_pick(PAYMENT_METHODS, bill.payment_method, self.lang)} · "
            f"{_pick(PAYMENT_STATUSES, bill.payment_status, self.lang)}"
        )
        rows = [
            (self.labels["date"], when),
            (self.labels["billed_to"], bill.customer_name or self.labels["neighbour"]),
        ]
        if bill.customer_phone:
            rows.append((self.labels["phone"], bill.customer_phone))
        rows.append((self.labels["payment"], payment))
        rows.append((self.labels["status"], _pick(ORDER_STATUSES, bill.status, self.lang)))
        label_w = 110
        for label, value in rows:
            y = self.get_y()
            self._font(9, "B", MUTED)
            self.set_xy(self.l_margin, y + 1)
            self.cell(label_w, 14, label)
            self._font(11, "", INK)
            self.set_xy(self.l_margin + label_w, y)
            self.multi_cell(self.content_w - label_w, 16, value, align="L", new_x="LMARGIN", new_y="NEXT")

    def _items_header(self, widths: list[float]) -> None:
        self.set_fill_color(*GOLD_SOFT)
        labels = [self.labels["item"], self.labels["qty"], self.labels["rate"], self.labels["amount"]]
        aligns = ["L", "R", "R", "R"]
        for width, label, align in zip(widths, labels, aligns, strict=True):
            self._font(9, "B", FOREST)
            self.cell(width, 22, f" {label} " if align == "L" else f"{label}  ", fill=True, align=align)
        self.ln(22)

    def _items(self) -> None:
        widths = [self.content_w - 60 - 100 - 110, 60, 100, 110]
        self._ensure_room(22 + 30)
        self._items_header(widths)
        currency = self.bill.currency
        for line in self.bill.lines:
            self._font(10, "", INK)
            name_lines = self.multi_cell(widths[0] - 8, 14, line.name, align="L", dry_run=True, output="LINES")
            row_h = max(1, len(name_lines)) * 14 + 10
            if self._ensure_room(row_h):
                self._items_header(widths)
            y = self.get_y()
            self._font(10, "", INK)
            self.set_xy(self.l_margin + 4, y + 5)
            self.multi_cell(widths[0] - 8, 14, line.name, align="L")
            x = self.l_margin + widths[0]
            cells = [
                str(line.quantity),
                format_money(line.unit_price, currency),
                format_money(line.unit_price * line.quantity, currency),
            ]
            for width, text in zip(widths[1:], cells, strict=True):
                self._font(10, "", INK)
                self.set_xy(x, y + 5)
                self.cell(width, 14, f"{text}  ", align="R")
                x += width
            self.set_draw_color(*RULE)
            self.set_line_width(0.5)
            self.line(self.l_margin, y + row_h, self.w - self.r_margin, y + row_h)
            self.set_xy(self.l_margin, y + row_h)

    def _totals(self) -> None:
        bill = self.bill
        rows: list[tuple[str, str]] = [(self.labels["subtotal"], format_money(bill.subtotal, bill.currency))]
        if bill.tax > 0:
            rows.append((gst_label(self.labels["tax"], bill.tax, bill.subtotal), format_money(bill.tax, bill.currency)))
        if bill.discount > 0:
            rows.append((self.labels["discount"], f"- {format_money(bill.discount, bill.currency)}"))
        if bill.shipping > 0:
            rows.append((self.labels["shipping"], format_money(bill.shipping, bill.currency)))
        box_h = 24 + len(rows) * 16 + 28
        self.ln(10)
        self._ensure_room(box_h)
        y = self.get_y()
        self.set_fill_color(*GOLD)
        self.rect(self.l_margin, y, self.content_w, box_h, style="F", round_corners=True, corner_radius=10)
        paid = bill.payment_status.strip().lower() == "paid"
        self._font(12, "B", FOREST)
        self.set_xy(self.l_margin + 16, y + 14)
        self.cell(self.content_w / 2, 16, self.labels["total_paid" if paid else "total_due"])
        right_w = 240
        right_x = self.w - self.r_margin - 16 - right_w
        row_y = y + 14
        for label, value in rows:
            self._font(10, "", FOREST)
            self.set_xy(right_x, row_y)
            self.cell(right_w - 110, 16, label, align="R")
            self._font(10, "", FOREST)
            self.cell(110, 16, value, align="R")
            row_y += 16
        self._font(15, "B", FOREST)
        self.set_xy(right_x, row_y + 6)
        self.cell(right_w - 130, 20, self.labels["total"], align="R")
        self._font(15, "B", FOREST)
        self.cell(130, 20, format_money(bill.total, bill.currency), align="R")
        self.set_xy(self.l_margin, y + box_h)

    def _notes(self) -> None:
        if not self.bill.notes:
            return
        self.ln(10)
        self._ensure_room(40)
        self._font(10, "B", MUTED)
        self.cell(0, 14, self.labels["notes"], new_x="LMARGIN", new_y="NEXT")
        self._font(10, "", INK)
        self.multi_cell(0, 14, self.bill.notes, align="L", new_x="LMARGIN", new_y="NEXT")

    def _signatures(self) -> None:
        self.ln(14)
        self._ensure_room(SIGNATURE_H)
        y = self.get_y()
        gap = 24
        box_w = (self.content_w - gap) / 2
        blocks = [
            (self.labels["customer_sign"], self.bill.customer_name or self.labels["neighbour"]),
            (self.labels["shop_sign"], self.bill.shop_name),
        ]
        for index, (title, name) in enumerate(blocks):
            x = self.l_margin + index * (box_w + gap)
            self.set_draw_color(*RULE)
            self.set_line_width(0.7)
            self.set_fill_color(*WHITE)
            self.rect(x, y, box_w, SIGNATURE_H, style="DF", round_corners=True, corner_radius=8)
            self._font(10, "B", FOREST)
            self.set_xy(x + 12, y + 10)
            self.cell(box_w - 24, 14, title)
            line_y = y + 52
            self.set_draw_color(*INK)
            self.set_line_width(0.6)
            self.line(x + 12, line_y, x + box_w - 12, line_y)
            self._font(9, "", MUTED)
            self.set_xy(x + 12, line_y + 5)
            self.cell(box_w - 24, 13, f"{self.labels['name']}: {name}")
            self._font(9, "", MUTED)
            self.set_xy(x + 12, line_y + 20)
            self.cell(box_w - 24, 13, f"{self.labels['sign_date']}: ____________________")
        self.set_xy(self.l_margin, y + SIGNATURE_H)

    def render(self) -> bytes:
        self.add_page()
        self._title_block()
        self._divider()
        self._meta_rows()
        self._divider(6)
        self._items()
        self._totals()
        self._notes()
        self._signatures()
        return bytes(self.output())


def render_order_bill(bill: BillInput, lang: str = "en") -> bytes:
    return _BillDocument(bill, "hi" if lang == "hi" else "en").render()
