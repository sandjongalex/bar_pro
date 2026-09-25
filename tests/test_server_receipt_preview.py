from sqlalchemy import select

from app.extensions import db
from app.models import StaffAssignment, utcnow
from app.order_services import order_service
from app.order_suborder_service import order_suborder_service
from app.shift_models import EmployeeShift
from test_cashier_complete_flow import _login
from test_workflows import env


def _put_server_on_duty(owner, bar, server):
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert assignment is not None
    db.session.add(
        EmployeeShift(
            bar_id=bar.id,
            staff_assignment_id=assignment.id,
            role_snapshot="SERVER",
            status="OPEN",
            started_at=utcnow(),
            started_by_id=owner.id,
        )
    )
    db.session.flush()
    return assignment


def test_server_can_preview_own_invoice_in_both_modes_without_print_controls(env):
    app, owner, bar, _, product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)

    value = order_service.create(
        server,
        bar.id,
        "SERVER-PREVIEW",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(owner, bar.id, value.id)

    validated = order_suborder_service.create_server_addition(
        server,
        bar.id,
        value.id,
        [{"product_id": product.id, "quantity": 1}],
        "Tour validé",
    )
    pending = order_suborder_service.create_cashier_addition(
        owner,
        bar.id,
        value.id,
        [{"product_id": product.id, "quantity": 1}],
        "Tour encore à valider",
    )
    db.session.commit()

    assert validated.status == "VALIDATED"
    assert pending.status == "PENDING_VALIDATION"

    client = app.test_client()
    _login(client, server.email)
    page = client.get(f"/bars/{bar.id}/orders/{value.id}/detail")

    assert page.status_code == 200
    assert "Aperçu de la facture" in page.text
    assert "Facture cumulée" in page.text
    assert "Facture détaillée" in page.text
    assert "Lecture seule" in page.text
    assert "l’impression reste réservée à la caisse" in page.text
    assert "server_invoice_preview.js" in page.text

    # Only original lines and VALIDATED sub-orders feed the customer invoice
    # preview. The pending cashier addition stays visible in history but is not
    # part of the invoice rows yet.
    assert page.text.count("data-server-invoice-line") == 2

    # A server receives no RawBT transport and no cashier print action.
    assert "data-rawbt-intent" not in page.text
    assert "🖨 Imprimer" not in page.text


def test_server_cannot_preview_an_order_not_assigned_to_them(env):
    app, owner, bar, _, product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)

    other_order = order_service.create(
        owner,
        bar.id,
        "OWNER-ONLY-PREVIEW",
        [{"product_id": product.id, "quantity": 1}],
    )
    db.session.commit()

    client = app.test_client()
    _login(client, server.email)
    page = client.get(f"/bars/{bar.id}/orders/{other_order.id}/detail")

    assert page.status_code == 404
