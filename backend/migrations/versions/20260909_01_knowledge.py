"""Create deterministic knowledge storage with frozen PostgreSQL DDL.

Revision ID: 20260909_01
Revises: 20260908_01
"""

from alembic import op

revision: str = "20260909_01"
down_revision: str | None = "20260908_01"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create normalized knowledge tables and the database-owned timestamp trigger."""
    op.execute("""
        CREATE TABLE knowledge_documents (
        id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        namespace VARCHAR(64) NOT NULL,
        source_key VARCHAR(200) NOT NULL,
        title VARCHAR(300) NOT NULL,
        media_type VARCHAR(100) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_knowledge_documents PRIMARY KEY (id),
        CONSTRAINT uq_knowledge_source UNIQUE (tenant_id, namespace, source_key),
        CONSTRAINT ck_knowledge_documents_namespace_valid CHECK (namespace ~
        '^[a-z][a-z0-9_-]{0,63}$'),
        CONSTRAINT ck_knowledge_documents_source_key_valid CHECK (source_key ~
        '^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$' AND position('..' in source_key) = 0),
        CONSTRAINT ck_knowledge_documents_title_not_blank CHECK (length(trim(title)) > 0),
        CONSTRAINT ck_knowledge_documents_media_type_valid CHECK (media_type IN ('text/plain',
        'text/markdown')),
        CONSTRAINT fk_knowledge_documents_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES
        tenants (id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE TABLE knowledge_document_revisions (
        id UUID NOT NULL,
        document_id UUID NOT NULL,
        revision_number INTEGER NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        normalized_content TEXT NOT NULL,
        normalized_bytes INTEGER NOT NULL,
        normalized_chars INTEGER NOT NULL,
        title VARCHAR(300) NOT NULL,
        media_type VARCHAR(100) NOT NULL,
        parser_version VARCHAR(64) NOT NULL,
        chunker_version VARCHAR(64) NOT NULL,
        config_sha256 VARCHAR(64) NOT NULL,
        max_chars INTEGER NOT NULL,
        overlap_chars INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_knowledge_document_revisions PRIMARY KEY (id),
        CONSTRAINT uq_knowledge_revision UNIQUE (document_id, revision_number),
        CONSTRAINT ck_knowledge_document_revisions_revision_positive CHECK (revision_number >
        0),
        CONSTRAINT ck_knowledge_document_revisions_hash_valid CHECK (content_sha256 ~
        '^[a-f0-9]{64}$'),
        CONSTRAINT ck_knowledge_document_revisions_sizes_positive CHECK (normalized_bytes > 0
        AND normalized_chars > 0),
        CONSTRAINT ck_knowledge_document_revisions_max_chars_valid CHECK (max_chars BETWEEN 64
        AND 16384),
        CONSTRAINT ck_knowledge_document_revisions_overlap_valid CHECK (overlap_chars >= 0 AND
        overlap_chars <= max_chars / 2),
        CONSTRAINT ck_knowledge_document_revisions_chunk_count_valid CHECK (chunk_count BETWEEN
        1 AND 8192),
        CONSTRAINT fk_knowledge_document_revisions_document_id_knowledge_documents FOREIGN
        KEY(document_id) REFERENCES knowledge_documents (id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE TABLE knowledge_chunks (
        id UUID NOT NULL,
        revision_id UUID NOT NULL,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        section_path TEXT[] NOT NULL,
        character_start INTEGER NOT NULL,
        character_end INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id),
        CONSTRAINT uq_knowledge_chunk_index UNIQUE (revision_id, chunk_index),
        CONSTRAINT ck_knowledge_chunks_index_nonnegative CHECK (chunk_index >= 0),
        CONSTRAINT ck_knowledge_chunks_hash_valid CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_knowledge_chunks_range_valid CHECK (character_start >= 0 AND character_end
        > character_start),
        CONSTRAINT ck_knowledge_chunks_range_length CHECK (length(content) = character_end -
        character_start),
        CONSTRAINT fk_knowledge_chunks_revision_id_knowledge_document_revisions FOREIGN
        KEY(revision_id) REFERENCES knowledge_document_revisions (id) ON DELETE RESTRICT
        )
    """)
    op.execute(
        "CREATE TRIGGER trg_knowledge_documents_updated_at BEFORE UPDATE ON knowledge_documents "
        "FOR EACH ROW EXECUTE FUNCTION agentopshub_touch_updated_at()"
    )


def downgrade() -> None:
    """Drop only Phase 5 tables in reverse dependency order."""
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_document_revisions")
    op.drop_table("knowledge_documents")
