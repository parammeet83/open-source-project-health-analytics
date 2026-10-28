"""Analytics views (8-view contract) and least-privilege grants.

Revision ID: 0003_analytics_views
Revises: 0002_source_tables
Create Date: 2026-10-28
"""

from alembic import op

from oss_health.sql_files import ANALYTICS_VIEWS, read_sql, sql_statements

revision = "0003_analytics_views"
down_revision = "0002_source_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in sql_statements("analytics_views.sql"):
        op.execute(statement)
    op.execute(read_sql("grants.sql"))


def downgrade() -> None:
    for view in reversed(ANALYTICS_VIEWS):
        op.execute(f"DROP VIEW IF EXISTS analytics.{view}")
