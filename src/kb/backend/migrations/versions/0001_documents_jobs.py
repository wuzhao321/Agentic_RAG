"""Create the document catalog and durable ingestion jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_documents_jobs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("project_name", sa.String(200), nullable=False),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_documents_size"),
        sa.CheckConstraint(
            "doc_type IN ('技术规范', '交付报告', '接口说明书', '其他')",
            name="ck_documents_type",
        ),
    )
    op.create_index("ix_documents_project_created", "documents", ["project_name", "created_at"])
    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'interrupted')",
            name="ck_ingest_jobs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_ingest_jobs_attempts"),
    )
    op.create_index("ix_ingest_jobs_queue", "ingest_jobs", ["status", "enqueued_at"])


def downgrade():
    op.drop_table("ingest_jobs")
    op.drop_table("documents")
