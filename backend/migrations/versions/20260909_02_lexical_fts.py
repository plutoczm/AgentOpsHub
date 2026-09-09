"""Add the PostgreSQL simple-configuration FTS expression index.

Revision ID: 20260909_02
Revises: 20260909_01
"""

from alembic import op

revision: str = "20260909_02"
down_revision: str | None = "20260909_01"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Index literal chunk content; scoped SQL separately excludes historical revisions."""
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks "
        "USING gin (to_tsvector('pg_catalog.simple'::regconfig, content))"
    )


def downgrade() -> None:
    """Remove only the new search index, preserving all document history."""
    op.drop_index("ix_knowledge_chunks_fts", table_name="knowledge_chunks")
