"""Command-line interface. ``oss-health run`` calls the shared ``run_pipeline()``.

Exit codes: 0 success · 1 failed · 2 partial_success · 3 not started (configuration/precondition).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from oss_health import __version__
from oss_health.config import ROLES_ENV_FILE, load_env_files, load_settings
from oss_health.errors import ConfigError, OssHealthError
from oss_health.logging_setup import configure_logging
from oss_health.redaction import register_secret, safe_error_message

logger = logging.getLogger("oss_health.cli")

URL_VARIABLES = (
    "MIGRATION_DATABASE_URL",
    "PIPELINE_DATABASE_URL",
    "API_DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
)
APP_ROLE = "oss_health_app"
READER_ROLE = "oss_health_reader"

# label -> (role, object, privilege, expected)
ROLE_PRIVILEGE_EXPECTATIONS = {
    "reader can read analytics": (READER_ROLE, "analytics.fact_pipeline_run", "SELECT", True),
    "reader cannot read raw tables": (READER_ROLE, "oss_health.github_issues", "SELECT", False),
    "reader cannot insert": (READER_ROLE, "oss_health.pipeline_runs", "INSERT", False),
    "app can insert issues": (APP_ROLE, "oss_health.github_issues", "INSERT", True),
    "app cannot delete issues": (APP_ROLE, "oss_health.github_issues", "DELETE", False),
    "app cannot truncate runs": (APP_ROLE, "oss_health.pipeline_runs", "TRUNCATE", False),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss-health", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="Path to pipeline.toml (default: backend/config)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the ingestion pipeline once")
    run.add_argument(
        "--repo",
        action="append",
        dest="repos",
        metavar="OWNER/NAME",
        help="Limit this run to the given repositories (repeatable)",
    )
    run.add_argument(
        "--full-refresh",
        action="store_true",
        help="Re-backfill the history window and all open items",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract, transform and validate without any database access",
    )
    run.add_argument("--trigger", choices=["manual", "scheduled", "ci"], default="manual")

    sub.add_parser("check-config", help="Validate configuration without network or DB access")

    db = sub.add_parser("db", help="Database administration (owner/migration credential)")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("upgrade", help="Apply Alembic migrations using MIGRATION_DATABASE_URL")
    bootstrap = db_sub.add_parser(
        "bootstrap-roles",
        help="Create/update oss_health_app and oss_health_reader (passwords from environment)",
    )
    bootstrap.add_argument(
        "--write-env",
        action="store_true",
        help="Generate missing passwords and write PIPELINE_DATABASE_URL / API_DATABASE_URL "
        "to the git-ignored .env.roles (values are never printed)",
    )
    db_sub.add_parser("verify-roles", help="Report the privileges of the runtime roles")
    check = db_sub.add_parser(
        "check-url",
        help="Secret-safe validation of a database URL variable (prints yes/no flags only)",
    )
    check.add_argument(
        "--env",
        dest="env_name",
        choices=URL_VARIABLES,
        help="Variable to check (default: the URL `db upgrade` would use)",
    )
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from oss_health.pipeline import EXIT_CODES, run_pipeline

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        configure_logging(args.log_level or "INFO")
        logger.error("Configuration error: %s", safe_error_message(exc))
        return EXIT_CODES["not_started"]
    logs_dir = settings.logs_path if settings.output.write_log_file else None
    configure_logging(args.log_level or settings.output.log_level, logs_dir)
    result = run_pipeline(
        settings,
        trigger=args.trigger,
        repositories=args.repos,
        full_refresh=args.full_refresh,
        dry_run=args.dry_run,
    )
    summary = ", ".join(f"{r.full_name}={r.status}" for r in result.repositories) or "none"
    print(f"status={result.status} exit_code={result.exit_code} run_id={result.run_id} "
          f"repositories: {summary} report={result.report_path}")  # fmt: skip
    return result.exit_code


def _cmd_check_config(args: argparse.Namespace) -> int:
    configure_logging(args.log_level or "INFO")
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", safe_error_message(exc))
        return 3
    public = settings.public_dict()
    print(f"Configuration OK: {len(settings.repositories)} repositories")
    for name in settings.repositories:
        print(f"  - {name}")
    print(f"history_days={public['extraction']['history_days']} "
          f"overlap_hours={public['extraction']['incremental_overlap_hours']} "
          f"github_token_configured={public['github_token_configured']} "
          f"pipeline_database_configured={settings.database_url is not None}")  # fmt: skip
    return 0


def _migration_url_source() -> tuple[str, str]:
    """(variable name, value) for the migration credential, in documented precedence.

    Neon's `neon env pull` provides DATABASE_URL_UNPOOLED: the project owner on the direct
    (non-pooled) endpoint, which is what Alembic and role management require.
    """
    for name in ("MIGRATION_DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        value = os.environ.get(name, "")
        if value.strip():
            return name, value
    return "MIGRATION_DATABASE_URL", ""


def _migration_url() -> str:
    url = _migration_url_source()[1].strip()
    if not url:
        raise ConfigError("MIGRATION_DATABASE_URL (or Neon's DATABASE_URL_UNPOOLED) is not set")
    from oss_health.db import normalize_database_url, password_from_url

    register_secret(password_from_url(url))
    # Fail fast, before Alembic or any connection: run `oss-health db check-url` for a diagnosis.
    normalize_database_url(url)
    return url


def _cmd_db(args: argparse.Namespace) -> int:
    configure_logging(args.log_level or "INFO")
    try:
        load_env_files()
        if args.db_command == "check-url":
            return _check_url(args.env_name)
        url = _migration_url()
        if args.db_command == "upgrade":
            from oss_health.migrate import upgrade

            upgrade(url)
            print("Migrations applied (head).")
            return 0
        if args.db_command == "bootstrap-roles":
            return _bootstrap_roles(url, write_env=args.write_env)
        if args.db_command == "verify-roles":
            return _verify_roles(url)
    except OssHealthError as exc:
        logger.error("%s", safe_error_message(exc))
        return 3
    except Exception as exc:
        logger.error("Database command failed: %s: %s", type(exc).__name__, safe_error_message(exc))
        return 1
    return 1


CHECK_LABELS = {
    "present": "value present",
    "surrounding_whitespace": "leading/trailing whitespace",
    "internal_whitespace": "whitespace inside the value",
    "surrounding_quotes": "wrapped in quotes",
    "key_prefix": "accidental KEY= prefix",
    "expected_scheme": "postgresql:// or postgres:// scheme",
    "parser_accepts": "accepted by the application's URL parser",
}


def _check_url(env_name: str | None) -> int:
    """Print only the variable name and boolean findings. Never the value or parser input."""
    from oss_health.db import diagnose_database_url

    if env_name:
        name, value = env_name, os.environ.get(env_name, "")
    else:
        name, value = _migration_url_source()
    report = diagnose_database_url(value)
    print(f"Checking {name} (value not shown)")
    for key, label in CHECK_LABELS.items():
        print(f"  {label}: {'yes' if report[key] else 'no'}")
    ok = (
        report["present"]
        and report["parser_accepts"]
        and report["expected_scheme"]
        and not report["surrounding_quotes"]
        and not report["key_prefix"]
        and not report["internal_whitespace"]
    )
    if ok:
        print(f"{name}: OK")
        return 0
    hints = []
    if not report["present"]:
        hints.append("the variable is empty or not set")
    if report["surrounding_quotes"]:
        hints.append("remove the surrounding quotes")
    if report["key_prefix"]:
        hints.append("store only the URL, without a NAME= prefix")
    if report["internal_whitespace"]:
        hints.append("remove spaces/newlines (a pasted `psql '...'` command is not a URL)")
    if report["present"] and not report["expected_scheme"]:
        hints.append("the value must start with postgresql:// (or postgres://)")
    print(f"{name}: INVALID - " + "; ".join(hints or ["the URL parser rejected the value"]))
    return 3


def role_url(owner_url: str, role: str, password: str) -> str:
    """Same host, database and TLS parameters as ``owner_url``, authenticated as ``role``."""
    from oss_health.db import normalize_database_url

    url = normalize_database_url(owner_url).set(
        drivername="postgresql", username=role, password=password
    )
    return url.render_as_string(hide_password=False)


def write_roles_env(owner_url: str, passwords: dict[str, str], path=None) -> None:
    """Write role credentials and URLs to the git-ignored .env.roles (never printed)."""
    path = path or ROLES_ENV_FILE
    lines = [
        "# Generated by `oss-health db bootstrap-roles --write-env`. Git-ignored. Do not commit.",
        f"OSS_HEALTH_APP_PASSWORD={passwords[APP_ROLE]}",
        f"OSS_HEALTH_READER_PASSWORD={passwords[READER_ROLE]}",
        f"PIPELINE_DATABASE_URL={role_url(owner_url, APP_ROLE, passwords[APP_ROLE])}",
        f"API_DATABASE_URL={role_url(owner_url, READER_ROLE, passwords[READER_ROLE])}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bootstrap_roles(url: str, *, write_env: bool = False) -> int:
    """Create or update the runtime roles. Passwords come from the environment, never argv."""
    import secrets

    from psycopg import sql
    from sqlalchemy import text

    from oss_health.db import create_db_engine
    from oss_health.sql_files import read_sql

    passwords = {
        APP_ROLE: os.environ.get("OSS_HEALTH_APP_PASSWORD", ""),
        READER_ROLE: os.environ.get("OSS_HEALTH_READER_PASSWORD", ""),
    }
    if write_env:
        # Reuse passwords already in .env.roles (loaded into the environment); generate the rest.
        for role in passwords:
            if not passwords[role]:
                passwords[role] = secrets.token_urlsafe(32)
    for role, password in passwords.items():
        if len(password) < 24:
            raise ConfigError(
                f"Set a strong password (>= 24 characters) for {role} in the environment"
            )
        register_secret(password)
    settings = {
        APP_ROLE: {"limit": 5, "statement_timeout": "300s", "read_only": False},
        READER_ROLE: {"limit": 10, "statement_timeout": "10s", "read_only": True},
    }
    engine = create_db_engine(url, application_name="oss-health-bootstrap")
    try:
        with engine.begin() as conn:
            raw = conn.connection.driver_connection
            for role, password in passwords.items():
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                ).first()
                verb = "ALTER" if exists else "CREATE"
                stmt = sql.SQL(
                    "{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {limit} PASSWORD {pw}"
                ).format(
                    verb=sql.SQL(verb),
                    role=sql.Identifier(role),
                    limit=sql.Literal(settings[role]["limit"]),
                    pw=sql.Literal(password),
                )
                with raw.cursor() as cur:
                    cur.execute(stmt)
                    cur.execute(
                        sql.SQL("ALTER ROLE {role} SET statement_timeout = {v}").format(
                            role=sql.Identifier(role),
                            v=sql.Literal(settings[role]["statement_timeout"]),
                        )
                    )
                    cur.execute(
                        sql.SQL("ALTER ROLE {role} SET default_transaction_read_only = {v}").format(
                            role=sql.Identifier(role),
                            v=sql.Literal("on" if settings[role]["read_only"] else "off"),
                        )
                    )
                print(f"{'Updated' if exists else 'Created'} role {role}")
            conn.execute(text(read_sql("grants.sql")))
        print("Grants applied.")
    finally:
        engine.dispose()
    if write_env:
        write_roles_env(url, passwords)
        print(f"Wrote PIPELINE_DATABASE_URL and API_DATABASE_URL to {ROLES_ENV_FILE.name}")
    return _verify_roles(url)


def _verify_roles(url: str) -> int:
    from sqlalchemy import text

    from oss_health.db import create_db_engine

    engine = create_db_engine(url, application_name="oss-health-verify")
    problems: list[str] = []
    try:
        with engine.connect() as conn:
            for role in (APP_ROLE, READER_ROLE):
                attrs = conn.execute(
                    text(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
                        "FROM pg_roles WHERE rolname = :r"
                    ),
                    {"r": role},
                ).first()
                if attrs is None:
                    problems.append(f"{role} does not exist")
                    continue
                if any(attrs):
                    problems.append(f"{role} has elevated role attributes")
                member_of = conn.execute(
                    text(
                        "SELECT string_agg(b.rolname, ',') FROM pg_auth_members m "
                        "JOIN pg_roles b ON b.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                        "WHERE u.rolname = :r"
                    ),
                    {"r": role},
                ).scalar_one()
                if member_of:
                    problems.append(f"{role} is a member of: {member_of}")
            for label, (role, obj, priv, expected) in ROLE_PRIVILEGE_EXPECTATIONS.items():
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                ).first()
                if not exists:
                    continue
                actual = conn.execute(
                    text("SELECT has_table_privilege(:r, :o, :p)"), {"r": role, "o": obj, "p": priv}
                ).scalar_one()
                print(f"[{'ok' if actual == expected else 'FAIL'}] {label}")
                if actual != expected:
                    problems.append(label)
    finally:
        engine.dispose()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    return 0 if not problems else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "check-config":
        return _cmd_check_config(args)
    return _cmd_db(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
