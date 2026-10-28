"""Alembic environment. Uses MIGRATION_DATABASE_URL (the schema-owner credential) only."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from oss_health.db import normalize_database_url

config = context.config


def _url():
    url = config.attributes.get("database_url") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is not set")
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(
        _url(),
        poolclass=pool.NullPool,
        connect_args={"application_name": "oss-health-migrations", "options": "-c timezone=UTC"},
    )
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
