"""Console + optional JSON-lines file logging with run context and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from oss_health.redaction import redact

run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
repository_var: contextvars.ContextVar[str] = contextvars.ContextVar("repository", default="-")


class ContextFilter(logging.Filter):
    """Attach run/repository context and redact secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.repository = repository_var.get()
        # Render the message once, redact it, and drop args so handlers cannot re-expand them.
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "-"),
            "repository": getattr(record, "repository", "-"),
            "message": record.getMessage(),
        }
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s [run=%(run_id)s repo=%(repository)s] %(message)s"


def configure_logging(level: str = "INFO", logs_dir: Path | None = None) -> None:
    """Configure the root logger. Safe to call more than once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level.upper())

    context_filter = ContextFilter()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    console.addFilter(context_filter)
    root.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"pipeline_{datetime.now(UTC):%Y%m%d}.log"
        file_handler = logging.FileHandler(logs_dir / file_name, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(context_filter)
        root.addHandler(file_handler)

    # Keep third-party HTTP/DB loggers quiet; they could otherwise echo URLs or parameters.
    for noisy in ("urllib3", "sqlalchemy.engine", "alembic.runtime.migration"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
