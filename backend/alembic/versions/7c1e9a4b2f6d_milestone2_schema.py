"""milestone 2 authorization schema

Revision ID: 7c1e9a4b2f6d
Revises: 9e395a628e6a
Create Date: 2026-11-05

Adds document metadata, document permissions, ABAC user attributes and the
append-only security audit table. It also backfills the EMPLOYEE role for
tenants created by Milestone 1.

RLS is intentionally NOT enabled here. Phase 7 adds the restricted runtime
role, FORCE ROW LEVEL SECURITY and tenant policies in a separate migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7c1e9a4b2f6d"
down_revision: Union[str, None] = "9e395a628e6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("document_type", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column(
            "required_clearance_level",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_documents_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "uploaded_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_documents_tenant_uploader",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            name="uq_documents_tenant_document",
        ),
        sa.CheckConstraint(
            "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_documents_classification",
        ),
        sa.CheckConstraint(
            "required_clearance_level BETWEEN 0 AND 5",
            name="ck_documents_clearance_range",
        ),
        sa.CheckConstraint(
            "required_clearance_level >= "
            "CASE classification "
            "WHEN 'PUBLIC' THEN 0 "
            "WHEN 'INTERNAL' THEN 1 "
            "WHEN 'CONFIDENTIAL' THEN 3 "
            "WHEN 'RESTRICTED' THEN 4 "
            "END",
            name="ck_documents_clearance_floor",
        ),
        sa.CheckConstraint(
            "length(trim(document_name)) > 0",
            name="ck_documents_name_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(document_type)) > 0",
            name="ck_documents_type_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(department)) > 0",
            name="ck_documents_department_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_documents_tenant_active", "documents", ["tenant_id", "is_active"], unique=False
    )
    op.create_index(
        "ix_documents_tenant_department",
        "documents",
        ["tenant_id", "department"],
        unique=False,
    )
    op.create_index(
        "ix_documents_tenant_classification",
        "documents",
        ["tenant_id", "classification"],
        unique=False,
    )
    op.create_index(
        "ix_documents_tenant_created_at",
        "documents",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_tenant_uploaded_by",
        "documents",
        ["tenant_id", "uploaded_by"],
        unique=False,
    )

    op.create_table(
        "user_attributes",
        sa.Column("attribute_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attribute_name", sa.String(length=50), nullable=False),
        sa.Column("attribute_value", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_user_attributes_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_user_attributes_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("attribute_id", name=op.f("pk_user_attributes")),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "attribute_name",
            name="uq_user_attributes_tenant_user_name",
        ),
        sa.CheckConstraint(
            "attribute_name IN ("
            "'department', 'clearance_level', 'employment_status', "
            "'location', 'designation'"
            ")",
            name="ck_user_attributes_name",
        ),
        sa.CheckConstraint(
            "length(trim(attribute_value)) > 0",
            name="ck_user_attributes_value_not_blank",
        ),
        sa.CheckConstraint(
            "attribute_name <> 'clearance_level' OR attribute_value ~ '^[0-5]$'",
            name="ck_user_attributes_clearance",
        ),
        sa.CheckConstraint(
            "attribute_name <> 'employment_status' "
            "OR attribute_value IN ('ACTIVE', 'INACTIVE', 'SUSPENDED', 'TERMINATED')",
            name="ck_user_attributes_employment_status",
        ),
    )
    op.create_index(
        op.f("ix_user_attributes_tenant_id"),
        "user_attributes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_attributes_tenant_user",
        "user_attributes",
        ["tenant_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "document_permissions",
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("permission_type", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column(
            "cross_department_allowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_document_permissions_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_document_permissions_tenant_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.role_id"],
            name="fk_document_permissions_tenant_role",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_permissions_tenant_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_permissions_tenant_granted_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "permission_id", name=op.f("pk_document_permissions")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "permission_id",
            name="uq_document_permissions_tenant_permission",
        ),
        sa.CheckConstraint(
            "num_nonnulls(role_id, user_id) = 1",
            name="ck_document_permissions_one_subject",
        ),
        sa.CheckConstraint(
            "permission_type IN ('READ', 'UPDATE', 'DELETE', 'MANAGE_PERMISSIONS')",
            name="ck_document_permissions_type",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_document_permissions_expiry",
        ),
        sa.CheckConstraint(
            "is_active OR revoked_at IS NOT NULL",
            name="ck_document_permissions_revoked",
        ),
    )
    op.create_index(
        op.f("ix_document_permissions_tenant_id"),
        "document_permissions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_permissions_tenant_document",
        "document_permissions",
        ["tenant_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_permissions_tenant_role",
        "document_permissions",
        ["tenant_id", "role_id"],
        unique=False,
        postgresql_where=sa.text("role_id IS NOT NULL"),
    )
    op.create_index(
        "ix_document_permissions_tenant_user",
        "document_permissions",
        ["tenant_id", "user_id"],
        unique=False,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_document_permissions_tenant_expires_at",
        "document_permissions",
        ["tenant_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_document_permissions_tenant_active",
        "document_permissions",
        ["tenant_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "uq_document_permissions_active_role",
        "document_permissions",
        ["tenant_id", "document_id", "role_id", "permission_type"],
        unique=True,
        postgresql_where=sa.text("is_active AND role_id IS NOT NULL"),
    )
    op.create_index(
        "uq_document_permissions_active_user",
        "document_permissions",
        ["tenant_id", "document_id", "user_id", "permission_type"],
        unique=True,
        postgresql_where=sa.text("is_active AND user_id IS NOT NULL"),
    )

    op.create_table(
        "security_audit_logs",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "policy",
            sa.String(length=64),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_security_audit_logs")),
        sa.CheckConstraint(
            "decision IN ('ALLOW', 'DENY')",
            name="ck_security_audit_logs_decision",
        ),
        sa.CheckConstraint(
            "length(trim(action)) > 0",
            name="ck_security_audit_logs_action_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(resource_type)) > 0",
            name="ck_security_audit_logs_resource_type_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_security_audit_logs_tenant_id"),
        "security_audit_logs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_tenant_timestamp",
        "security_audit_logs",
        ["tenant_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_tenant_user_timestamp",
        "security_audit_logs",
        ["tenant_id", "user_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_tenant_resource",
        "security_audit_logs",
        ["tenant_id", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_tenant_action_decision_timestamp",
        "security_audit_logs",
        ["tenant_id", "action", "decision", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_request_id",
        "security_audit_logs",
        ["request_id"],
        unique=False,
    )

    # Backfill the new EMPLOYEE role for tenants created by Milestone 1.
    # gen_random_uuid() is built into PostgreSQL 13+.
    op.execute(
        sa.text(
            """
            INSERT INTO roles (role_id, tenant_id, role_name, description)
            SELECT
                gen_random_uuid(),
                o.tenant_id,
                'EMPLOYEE',
                'Standard employee with explicitly granted document access'
            FROM organizations o
            WHERE NOT EXISTS (
                SELECT 1
                FROM roles r
                WHERE r.tenant_id = o.tenant_id
                  AND r.role_name = 'EMPLOYEE'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("security_audit_logs")
    op.drop_table("document_permissions")
    op.drop_table("user_attributes")
    op.drop_table("documents")

    # Never delete a role that is still assigned to users. If EMPLOYEE is in
    # use, leaving it in place is safer than silently destroying assignments.
    op.execute(
        sa.text(
            """
            DELETE FROM roles r
            WHERE r.role_name = 'EMPLOYEE'
              AND NOT EXISTS (
                  SELECT 1
                  FROM user_roles ur
                  WHERE ur.role_id = r.role_id
              )
            """
        )
    )
