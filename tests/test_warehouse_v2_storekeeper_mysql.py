"""Real MySQL 8 migration test: preexisting staff survive a role constraint change."""
import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, select

from app import create_app
from app.extensions import db
from app.models import Bar, StaffAssignment, User, utcnow
from app.warehouse_permission_models import WarehouseStaffGrant


@pytest.mark.skipif(not os.getenv("WAREHOUSE_MYSQL_TEST_URI"), reason="MySQL CI not enabled")
def test_mysql_upgrade_preserves_existing_staff_and_adds_storekeeper(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URI", os.environ["WAREHOUSE_MYSQL_TEST_URI"])
    app = create_app("testing")
    with app.app_context():
        upgrade(revision="c6f2d4e8a1b0", directory=app.config["MIGRATIONS_DIRECTORY"])
        owner = User(email="mysql-role-owner@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("test-password")
        existing = User(email="mysql-existing-cashier@example.invalid", display_name="Cashier", category="EMPLOYEE")
        existing.set_password("test-password")
        db.session.add_all([owner, existing])
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="MySQL role migration", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        old = StaffAssignment(
            bar_id=bar.id, user_id=existing.id, role="CASHIER", started_at=utcnow()
        )
        db.session.add(old)
        db.session.commit()
        owner_id, user_id, bar_id, old_id = owner.id, existing.id, bar.id, old.id

        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.get(User, user_id).email == "mysql-existing-cashier@example.invalid"
        assert db.session.get(StaffAssignment, old_id).role == "CASHIER"
        assert db.session.get(Bar, bar_id).owner_id == owner_id

        keeper = User(email="mysql-storekeeper@example.invalid", display_name="Storekeeper", category="EMPLOYEE")
        keeper.set_password("test-password")
        db.session.add(keeper)
        db.session.flush()
        assigned = StaffAssignment(
            bar_id=bar_id, user_id=keeper.id, role="STOREKEEPER", started_at=utcnow()
        )
        db.session.add(assigned)
        db.session.commit()
        assert db.session.get(StaffAssignment, assigned.id).role == "STOREKEEPER"
        assert "warehouse_staff_grants" in inspect(db.engine).get_table_names()
        checks = {c["name"] for c in inspect(db.engine).get_check_constraints("staff_assignments")}
        assert "ck_staff_role" in checks
        assert db.session.scalar(select(WarehouseStaffGrant.id).where(WarehouseStaffGrant.bar_id == bar_id)) is None
        db.session.remove()
        db.engine.dispose()
