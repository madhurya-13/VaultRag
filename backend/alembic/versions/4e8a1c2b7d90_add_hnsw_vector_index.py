"""add hnsw vector index for cosine retrieval

Revision ID: 4e8a1c2b7d90
Revises: 1f07b971535a
Create Date: 2026-11-09 00:00:00.000000

The index is an optional performance aid. Tenant, document-state, embedding
model and permission predicates remain authoritative and are still applied in
SQL and by PostgreSQL RLS.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4e8a1c2b7d90"
down_revision: Union[str, None] = "1f07b971535a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF to_regtype('vector') IS NULL THEN
                    RAISE EXCEPTION
                        'pgvector is missing. Install it in the extensions schema '
                        'before applying this migration.';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_document_chunks_embedding_hnsw "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    )
