import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, render_template, request

from app.auth import api_required
from app.extensions import db
from app.models import Bar
from app.report_services import consolidated, summary

bp = Blueprint("reports", __name__, url_prefix="/api/v1/bars/<int:bar_id>/reports")

METHOD_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement bancaire",
    "OTHER": "Autre",
}


@bp.get("/summary")
@api_required
def report_summary(bar_id):
    try:
        return jsonify(
            {
                "success": True,
                "data": summary(request.api_user, bar_id, request.args.get("start"), request.args.get("end")),
                "meta": {},
            }
        )
    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "error": {"code": str(exc), "message": "Rapport invalide", "details": None},
            }
        ), 422


@bp.get("/csv")
@api_required
def report_csv(bar_id):
    data = summary(request.api_user, bar_id, request.args.get("start"), request.args.get("end"))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["section", "metric", "value"])
    for section, values in data.items():
        if isinstance(values, dict):
            for key, value in values.items():
                if not isinstance(value, (dict, list)):
                    writer.writerow([section, key, value])
    start = request.args.get("start") or "all"
    end = request.args.get("end") or "all"
    return Response(
        "\ufeff" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report_{start}_{end}.csv"},
    )


@bp.get("/print")
@api_required
def report_print(bar_id):
    start = request.args.get("start")
    end = request.args.get("end")
    data = summary(request.api_user, bar_id, start, end)
    bar = db.session.get(Bar, bar_id)
    generated_at = datetime.now(ZoneInfo(bar.timezone)) if bar else datetime.now()
    return render_template(
        "report_print.html",
        report=data,
        bar=bar,
        bar_id=bar_id,
        start=start or "Toutes",
        end=end or "Toutes",
        generated_at=generated_at,
        method_labels=METHOD_LABELS,
    )


@bp.get("/consolidated")
@api_required
def consolidated_report(bar_id=None):
    try:
        data = consolidated(request.api_user, request.args.get("start"), request.args.get("end"))
        return jsonify({"success": True, "data": data, "meta": {}})
    except PermissionError:
        return jsonify(
            {
                "success": False,
                "error": {"code": "FORBIDDEN", "message": "Accès refusé", "details": None},
            }
        ), 403
