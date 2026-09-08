"""Create tenant and ticket persistence with database-owned timestamps.

Revision ID: 20260908_01
Revises: none
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_01"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the two tables, deterministic constraints/indexes and update triggers."""
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.CheckConstraint("length(trim(slug)) > 0", name=op.f("ck_tenants_slug_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_tenants_name_not_blank")),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(11), server_default="open", nullable=False),
        sa.Column("priority", sa.String(8), server_default="medium", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tickets"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tickets_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name=op.f("ck_tickets_ticket_status"),
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name=op.f("ck_tickets_ticket_priority"),
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_tickets_title_not_blank")),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_tenant_id_status", "tickets", ["tenant_id", "status"])
    op.execute("""
        CREATE FUNCTION agentopshub_touch_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$
    """)
    for table in ("tenants", "tickets"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION agentopshub_touch_updated_at()"
        )


def downgrade() -> None:
    """Remove only objects owned by this revision, in dependency order."""
    op.drop_table("tickets")
    op.drop_table("tenants")
    op.execute("DROP FUNCTION agentopshub_touch_updated_at()")
