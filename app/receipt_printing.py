"""POS-58MINI receipts: 384 dots / font A (12 dots) = 32 columns.

RawBT's documented base64 transport sends ESC/POS bytes without re-encoding.
ASCII transliteration avoids depending on a clone's accented-character table.
"""
import base64
import textwrap
import unicodedata

WIDTH = 32


def printable(value):
    value = str(value or "").replace("œ", "oe").replace("Œ", "OE")
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    value = value.encode("ascii", "replace").decode("ascii")
    # No user-supplied ESC/POS commands, line breaks or other control characters.
    return " ".join("".join(c if 32 <= ord(c) < 127 else " " for c in value).split())


def number(value):
    return format(value, ",f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, ",f")


def rawbt_receipt(*, bar, order, lines, payments, refunds, balance, server_name,
                  customer_name, payment_times, refund_times, payment_labels,
                  sale_time, issued_at):
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
    if bar.address:
        add(bar.address)
    if bar.phone:
        add(f"Tel: {bar.phone}")
    add("RECU DE VENTE")
    rule()
    add(f"Commande: {order.reference}")
    if customer_name:
        add(f"Client: {customer_name}")
    add(f"Date: {sale_time}")
    add(f"Serveuse: {server_name}")
    if order.table_label_snapshot:
        add(f"Table: {order.table_label_snapshot}")
    rule()
    for line in lines:
        add(line.product_name_snapshot)
        add(f"{number(line.quantity)} x {number(line.unit_sale_price_snapshot)}")
        amount("Total", line.total_amount)
    rule()
    amount("Total commande", order.total_amount)
    if balance["return_credit"] > 0:
        amount("Retours / avoirs", -balance["return_credit"])
    amount("NET A REGLER", balance["net_sale"])
    rule()
    for payment in payments:
        amount(payment_labels.get(payment.method, payment.method), payment.amount_applied, payment.currency)
        add(f"{payment.reference} {payment_times[payment.id]}")
        if payment.method == "CASH" and payment.change_given > 0:
            amount("Recu", payment.amount_presented, payment.currency)
            amount("Monnaie", payment.change_given, payment.currency)
    if balance["customer_credit"] > 0:
        amount("Credit client", balance["customer_credit"])
    for refund in refunds:
        amount("Remboursement " + payment_labels.get(refund.method, refund.method), -refund.amount, refund.currency)
        add(f"{refund.reference} {refund_times[refund.id]}")
    amount("Regle / finance", balance["net_settled"])
    amount("RESTE A PAYER", balance["amount_due"])
    if order.status == "CANCELLED":
        add("COMMANDE ANNULEE")
    elif balance["amount_due"] == 0:
        add("A CREDIT" if balance["customer_credit"] > 0 else "PAYEE")
    else:
        add("PAIEMENT PARTIEL" if balance["net_settled"] > 0 else "A PAYER")
    if order.notes:
        rule()
        add(f"Note: {order.notes}")
    rule()
    add("Merci pour votre visite.")
    add(f"Edite le {issued_at}")
    text = "\n".join(rows) + "\n\n\n"
    # Initialize, select normal font A and left alignment. No cutter on mobile printers.
    payload = b"\x1b@\x1b!\x00\x1ba\x00" + text.encode("ascii")
    encoded = base64.b64encode(payload).decode("ascii")
    intent = "intent:base64," + encoded + "#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;"
    return intent
