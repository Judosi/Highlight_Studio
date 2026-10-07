"""Accounts, sessions, RBAC, audit and PostgreSQL jobs.

Revision ID: 20260712_0001
Revises:
"""

from alembic import op

from backend.src.highlight_studio.infrastructure.database.models import Base

revision = "20260712_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is the initial schema. Models are authoritative and create_all is
    # intentionally used once here so SQLite integration tests and PostgreSQL
    # production create exactly the same tables and constraints.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # Reverse dependency order matters because several tables reference users
    # and projects with foreign keys.
    for table in [
        "audit_logs",
        "auth_rate_limits",
        "user_preferences",
        "account_tokens",
        "project_memberships",
        "jobs",
        "projects",
        "auth_sessions",
        "users",
    ]:
        op.drop_table(table)
