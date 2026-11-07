"""Compact, tamper-resistant RawBT/ESC-POS output for change vouchers.

This reuses the same 58/80mm typography as sales tickets; printing does not
change the voucher balance or issue an additional settlement.
"""
from __future__ import annotations

import base64
import textwrap
from decimal import Decimal

from app.receipt_printing import PRINT_STYLES, number, printable, receipt_columns


def rawbt_change_voucher_intent(bar, voucher, issuer, transactions=()):
    """Generate ASCII-only thermal bytes for a persisted voucher."""
    width = receipt_columns(bar)
    rows = []

    def add(value, style="normal"):
        capacity = width // 2 if style == "large" else width
        for part in textwrap.wrap(printable(value), capacity) or [""]:
            rows.append((part, style))

    def amount(label, value, currency):
        value = f"{label}: {number(Decimal(value))} {currency}"
        add(value, "large" if len(printable(value)) <= width // 2 else "tall")

    def rule():
        rows.append(("-" * width, "normal"))

    is_advance = voucher.code.startswith("AV-")
    title = printable(bar.name)
    add(title, "large" if len(title) <= width // 2 else "tall")
    add("BON D'AVANCE" if is_advance else "BON DE MONNAIE", "bold")
    rule()
    amount("SOLDE", voucher.balance_amount, voucher.currency)
    add(voucher.code, "bold")
    state = {
        "ACTIVE": "ACTIF", "PARTIAL": "PARTIEL",
        "SETTLED": "SOLDE", "CANCELLED": "ANNULE",
    }.get(voucher.status, voucher.status)
    add(state, "bold")
    rule()
    add(f"Initial: {number(voucher.initial_amount)} {voucher.currency}")
    add(f"Date: {voucher.issued_at.strftime('%d/%m/%Y %H:%M') if voucher.issued_at else '-'}")
    add(f"Facture: {voucher.origin_order_id}")
    if issuer:
        add(f"Caisse: {issuer.display_name}")
    if voucher.customer_name:
        add(f"Client: {voucher.customer_name}")
    if voucher.customer_phone:
        add(f"Telephone: {voucher.customer_phone}")
    if transactions:
        rule()
        add("MOUVEMENTS", "bold")
        for item in transactions:
            label = "Utilise" if item.kind == "REDEEM" else "Rembourse"
            add(f"{label}: -{number(item.amount)} {item.currency}")
    rule()
    if is_advance:
        add("Valable sur sa commande d'origine.")
    else:
        add("A utiliser sur un achat ou a rembourser en especes.")
    add("Le solde enregistre fait foi.")
    add("Merci pour votre visite.")
    payload = bytearray(b"\x1b@\x1b!\x00\x1ba\x00")
    for text, style in rows:
        payload.extend(b"\x1b3" + bytes([54 if style in {"tall", "large"} else 32]))
        payload.extend(PRINT_STYLES[style])
        payload.extend(text.encode("ascii"))
        payload.extend(b"\n")
    payload.extend(b"\x1b2\x1bE\x00\x1d!\x00\n\n\n")
    encoded = base64.b64encode(bytes(payload)).decode("ascii")
    return "intent:base64," + encoded + "#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;"
