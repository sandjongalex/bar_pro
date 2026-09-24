"""Add employee attendance and on-duty shifts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def _types():
    dialect = op.get_bind().dialect.name
    id_type = sa.Integer() if dialect == "sqlite" else sa.BigInteger()
    dt_type = sa.DateTime() if dialect == "sqlite" else mysql.DATETIME(fsp=6)
    return id_type, dt_type


def upgrade():
    id_type, dt_type = _types()

    op.create_table(
        "employee_shifts",
        sa.Column("id", id_type, primary_key=True, nullable=False),
        sa.Column("staff_assignment_id", id_type, nullable=False),
        sa.Column("role_snapshot", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("scheduled_start_at", dt_type, nullable=True),
        sa.Column("started_at", dt_type, nullable=False),
        sa.Column("ended_at", dt_type, nullable=True),
        sa.Column("started_by_id", id_type, nullable=False),
        sa.Column("ended_by_id", id_type, nullable=True),
        sa.Column(
            "open_staff_assignment_id",
            id_type,
            sa.Computed("CASE WHEN status = 'OPEN' THEN staff_assignment_id ELSE NULL END"),
            nullable=True,
        ),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.Column("bar_id", id_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ended_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "staff_assignment_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bar_id", "id", name="uq_employee_shifts_bar_id_id"),
        sa.UniqueConstraint(
            "bar_id",
            "open_staff_assignment_id",
            name="uq_employee_shifts_one_open_assignment",
        ),
        sa.CheckConstraint(
            "role_snapshot IN ('CASHIER','SERVER')",
            name="ck_employee_shifts_role",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','CLOSED')",
            name="ck_employee_shifts_status",
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND ended_at IS NULL AND ended_by_id IS NULL) OR "
            "(status = 'CLOSED' AND ended_at IS NOT NULL AND ended_by_id IS NOT NULL)",
            name="ck_employee_shifts_close_state",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_employee_shifts_dates",
        ),
    )
    op.create_index(
        "ix_employee_shifts_bar_created",
        "employee_shifts",
        ["bar_id", "created_at", "id"],
    )
    op.create_index(
        "ix_employee_shifts_assignment_status",
        "employee_shifts",
        ["bar_id", "staff_assignment_id", "status", "started_at", "id"],
    )
    op.create_index(
        "ix_employee_shifts_bar_status_role",
        "employee_shifts",
        ["bar_id", "status", "role_snapshot", "started_at", "id"],
    )


def downgrade():
    op.drop_index("ix_employee_shifts_bar_status_role", table_name="employee_shifts")
    op.drop_index("ix_employee_shifts_assignment_status", table_name="employee_shifts")
    op.drop_index("ix_employee_shifts_bar_created", table_name="employee_shifts")
    op.drop_table("employee_shifts")
