"""POS-58MINI receipts: 384 dots / font A (12 dots) = 32 columns.

RawBT's documented base64 transport sends ESC/POS bytes without re-encoding.
ASCII transliteration avoids depending on a clone's accented-character table.
"""
import base64
import re
import textwrap
import unicodedata
from decimal import Decimal
from types import SimpleNamespace

WIDTH = 32


class ReceiptPrintOptions(str):
    """Backward-compatible detailed intent with extra print choices for the UI."""

    def __new__(cls, detailed, cumulative, cumulative_lines):
        value = super().__new__(cls, detailed)
        value.detailed = detailed
        value.cumulative = cumulative
        value.cumulative_lines = cumulative_lines
        return value


def printable(value):
    value = str(value or "").replace("œ", "oe").replace("Œ", "OE")
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    value = value.encode("ascii", "replace").decode("ascii")
    # No user-supplied ESC/POS commands, line breaks or other control characters.
    return " ".join("".join(c if 32 <= ord(c) < 127 else " " for c in value).split())


def number(value):
    return format(value, ",f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, ",f")


def cumulative_receipt_lines(lines):
    """Group identical products sold at the same unit price for a compact invoice.

    The unit price is part of the grouping key so a product sold at two different
    prices is never silently merged into a mathematically ambiguous line.
    """
    grouped = {}
    ordered_keys = []

    for line in lines:
        unit_price = Decimal(str(line.unit_sale_price_snapshot))
        key = (
            getattr(line, "product_id", None),
            str(line.product_name_snapshot),
            str(getattr(line, "unit_snapshot", "")),
            unit_price,
        )
        if key not in grouped:
            grouped[key] = SimpleNamespace(
                product_id=getattr(line, "product_id", None),
                product_name_snapshot=line.product_name_snapshot,
                unit_snapshot=getattr(line, "unit_snapshot", ""),
                quantity=Decimal("0"),
                unit_sale_price_snapshot=unit_price,
                total_amount=Decimal("0"),
            )
            ordered_keys.append(key)

        item = grouped[key]
        item.quantity += Decimal(str(line.quantity))
        item.total_amount += Decimal(str(line.total_amount))

    return [grouped[key] for key in ordered_keys]


def compact_order_reference(reference):
    """Short customer ID for generated CMD codes, keeping full source ID in DB."""
    value = str(reference or "")
    matched = re.fullmatch(r"CMD-\d{8}-(\d{6})-([A-Za-z0-9]{3,12})", value)
    return "-".join(matched.groups()) if matched else value


def receipt_address(bar):
    """Omit merchant address if its text is already part of the merchant name."""
    address = str(getattr(bar, "address", "") or "").strip()
    name = printable(getattr(bar, "name", "")).casefold()
    return None if address and printable(address).casefold() in name else address or None


def _rawbt_receipt_intent(*, bar, order, lines, payments, refunds, balance, server_name,
                          customer_name, payment_times, refund_times, payment_labels,
                          sale_time, issued_at, mode_label, receipt_sections=None,
                          open_advance=False, advance_balance=Decimal("0")):
    """Compact RawBT receipt. Internal references remain in the application."""
    rows = []

    def add(value):
        rows.extend(textwrap.wrap(printable(value), WIDTH) or [""])

    def amount(label, value, currency=None):
        currency = currency or order.currency
        currency = "FCFA" if currency in ("XAF", "XOF") else currency
        add(f"{label}: {number(value)} {currency}")

    def rule():
        rows.append("-" * WIDTH)

    add(bar.name)
    address = receipt_address(bar)
    if address:
        add(address)
    if bar.phone:
        add(f"Tel: {bar.phone}")
    add(sale_time)
    add(f"N {compact_order_reference(order.reference)}")
    if customer_name:
        add(f"Client: {customer_name}")
    if server_name and server_name != "Sans serveuse":
        add(f"Serveuse: {server_name}")
    if order.table_label_snapshot:
        add(f"Table: {order.table_label_snapshot}")
    rule()

    sections = receipt_sections if receipt_sections is not None else [{"label": None, "lines": lines}]
    multiple_rounds = len(sections) > 1
    for section in sections:
        if multiple_rounds and section["label"]:
            add(section.get("print_label") or section["label"])
        for line in section["lines"]:
            add(line.product_name_snapshot)
            left = f"{number(line.quantity)} x {number(line.unit_sale_price_snapshot)}"
            right = number(line.total_amount)
            if len(left) + len(right) + 1 <= WIDTH:
                rows.append(left + " " * (WIDTH - len(left) - len(right)) + right)
            else:
                add(f"{left} = {right}")
        if multiple_rounds:
            rule()
    if not multiple_rounds:
        rule()

    if balance["return_credit"] > 0:
        amount("Articles", order.total_amount)
        amount("Avoirs", -balance["return_credit"])
    if balance["amount_due"] > 0 and balance["net_settled"] <= 0:
        amount("A PAYER", balance["amount_due"])
    else:
        amount("TOTAL", balance["net_sale"])

    if payments or balance["customer_credit"] > 0 or refunds:
        rule()
        for payment in payments:
            label = payment_labels.get(payment.method, payment.method)
            if payment.method == "MOBILE_MONEY" and payment.provider_code:
                provider = (
                    "MTN" if payment.provider_code == "MTN"
                    else "Orange" if payment.provider_code == "ORANGE"
                    else printable(payment.provider_code)
                )
                label += f" {provider}"
            amount(label, payment.amount_applied, payment.currency)
            if payment.method == "CASH" and payment.change_given > 0:
                amount("Recu", payment.amount_presented, payment.currency)
                amount("Monnaie", payment.change_given, payment.currency)
            elif payment.method == "CASH" and payment.amount_presented > payment.amount_applied:
                amount("Montant remis", payment.amount_presented, payment.currency)
        if balance["customer_credit"] > 0:
            amount("Credit client", balance["customer_credit"])
        for refund in refunds:
            amount(
                "Remboursement " + payment_labels.get(refund.method, refund.method),
                -refund.amount, refund.currency,
            )

    if balance["amount_due"] > 0 and balance["net_settled"] > 0:
        rule()
        amount("RESTE A PAYER", balance["amount_due"])

    if order.status == "CANCELLED":
        add("COMMANDE ANNULEE")
    elif open_advance:
        add("COMMANDE EN COURS")
        amount("Avance disponible", advance_balance)
    elif balance["amount_due"] == 0:
        add("A CREDIT" if balance["customer_credit"] > 0 else "PAYEE")

    rule()
    add("Merci pour votre visite.")
    text = "\n".join(rows) + "\n\n\n"
    # Initialize, font A, left alignment; no cutter on mobile printers.
    payload = b"\x1b@\x1b!\x00\x1ba\x00" + text.encode("ascii")
    encoded = base64.b64encode(payload).decode("ascii")
    return "intent:base64," + encoded + "#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;"

def rawbt_receipt(*, bar, order, lines, payments, refunds, balance, server_name,
                  customer_name, payment_times, refund_times, payment_labels,
                  sale_time, issued_at, receipt_sections=None,
                  open_advance=False, advance_balance=Decimal("0")):
    cumulative_lines = cumulative_receipt_lines(lines)
    common = dict(
        bar=bar,
        order=order,
        payments=payments,
        refunds=refunds,
        balance=balance,
        server_name=server_name,
        customer_name=customer_name,
        payment_times=payment_times,
        refund_times=refund_times,
        payment_labels=payment_labels,
        sale_time=sale_time,
        issued_at=issued_at,
        open_advance=open_advance,
        advance_balance=advance_balance,
    )
    detailed = _rawbt_receipt_intent(
        lines=lines,
        receipt_sections=receipt_sections,
        mode_label="FACTURE DETAILLEE",
        **common,
    )
    cumulative = _rawbt_receipt_intent(
        lines=cumulative_lines,
        mode_label="FACTURE CUMULEE",
        **common,
    )
    return ReceiptPrintOptions(detailed, cumulative, cumulative_lines)
