"""Server-rendered operational reports for owners and bar administrators."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Bar
from app.permissions import permissions
from app.report_services import summary

bp = Blueprint("reports_web", __name__, url_prefix="/bars/<int:bar_id>/reports")

METHOD_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement bancaire",
    "OTHER": "Autre",
}


def _default_dates(bar: Bar):
    today = datetime.now(ZoneInfo(bar.timezone)).date()
    return today.replace(day=1).isoformat(), today.isoformat()


def _requested_period(bar: Bar):
    default_start, default_end = _default_dates(bar)
    start = (request.args.get("start") or default_start).strip()
    end = (request.args.get("end") or default_end).strip()
    return start, end


def _bar(bar_id: int):
    permissions.require(current_user, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    return bar


@bp.get("")
@login_required
def desk(bar_id: int):
    bar = _bar(bar_id)
    start, end = _requested_period(bar)
    try:
        report = summary(current_user, bar_id, start, end)
    except ValueError:
        flash("La période sélectionnée est invalide. Vérifiez les dates de début et de fin.", "danger")
        default_start, default_end = _default_dates(bar)
        return redirect(url_for("reports_web.desk", bar_id=bar_id, start=default_start, end=default_end))

    return render_template(
        "reports.html",
        bar=bar,
        report=report,
        start=start,
        end=end,
        method_labels=METHOD_LABELS,
    )


@bp.get("/csv")
@login_required
def export_csv(bar_id: int):
    bar = _bar(bar_id)
    start, end = _requested_period(bar)
    report = summary(current_user, bar_id, start, end)

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Rapport opérationnel", bar.name])
    writer.writerow(["Période", start, end])
    writer.writerow(["Devise", report["currency"]])
    writer.writerow([])

    writer.writerow(["INDICATEURS", "VALEUR"])
    metrics = [
        ("Ventes nettes", report["sales"]["revenue"]),
        ("Ventes brutes", report["sales"]["gross_revenue"]),
        ("Retours", report["sales"]["returns"]),
        ("Commandes soldées", report["sales"]["orders"]),
        ("Marge brute estimée", report["sales"]["gross_margin_estimate"]),
        ("Bénéfice estimé après dépenses", report["sales"]["estimated_profit_after_expenses"]),
        ("Encaissements nets", report["payments"]["net_received"]),
        ("Dépenses nettes", report["expenses"]["total"]),
        ("Créances clients actuelles", report["receivables"]["customer_credit_total"]),
        ("Dettes fournisseurs actuelles", report["supplier_payables"]["total_due"]),
        ("Stock total", report["stock"]["quantity"]),
        ("Valeur stock achat", report["stock"]["purchase_value"]),
        ("Valeur stock vente", report["stock"]["sale_value"]),
        ("Pertes stock période", report["stock"]["losses"]),
        ("Écart caisse cumulé", report["cash"]["closing_difference_total"]),
    ]
    writer.writerows(metrics)

    writer.writerow([])
    writer.writerow(["ENCAISSEMENTS PAR MODE", "MONTANT NET"])
    for method, amount in report["payments"]["by_method"].items():
        writer.writerow([METHOD_LABELS.get(method, method), amount])

    writer.writerow([])
    writer.writerow(["TOP PRODUITS", "QUANTITÉ NETTE"])
    for item in report["top_products"]:
        writer.writerow([item["name"], item["quantity"]])

    writer.writerow([])
    writer.writerow(["PERFORMANCE SERVEUSES", "COMMANDES", "VENTES", "CRÉDIT", "HORS CRÉDIT"])
    for item in report["servers"]:
        writer.writerow([
            item["name"],
            item["orders"],
            item["sales"],
            item["credit_sales"],
            item["non_credit_sales"],
        ])

    writer.writerow([])
    writer.writerow(["CRÉANCES CLIENTS", "MONTANT DÛ"])
    for item in report["receivables"]["customer_accounts"]:
        writer.writerow([item["customer"], item["amount_due"]])

    writer.writerow([])
    writer.writerow(["STOCKS FAIBLES", "CATÉGORIE", "QUANTITÉ", "SEUIL", "ÉCART"])
    for item in report["stock"]["low"]:
        writer.writerow([
            item["product"],
            item["category"] or "",
            item["quantity"],
            item["threshold"],
            item["difference"],
        ])

    filename = f"rapport_{bar_id}_{start}_{end}.csv"
    return Response(
        "\ufeff" + out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.get("/print")
@login_required
def printable(bar_id: int):
    bar = _bar(bar_id)
    start, end = _requested_period(bar)
    report = summary(current_user, bar_id, start, end)
    generated_at = datetime.now(ZoneInfo(bar.timezone))
    return render_template(
        "report_print.html",
        report=report,
        bar=bar,
        bar_id=bar_id,
        start=start,
        end=end,
        generated_at=generated_at,
        method_labels=METHOD_LABELS,
    )
