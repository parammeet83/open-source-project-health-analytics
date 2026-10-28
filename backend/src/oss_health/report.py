"""JSON quality report, written atomically after every run (including failed/not-started runs)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oss_health.redaction import redact

REPORT_VERSION = "1.0"
LATEST_NAME = "latest_quality_report.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_report(report: dict[str, Any], reports_dir: Path) -> Path:
    """Write ``quality_report_<run_id>.json`` and refresh ``latest_quality_report.json``."""
    report = {"report_version": REPORT_VERSION, **report}
    # Redact the serialized document as a whole: no secret can survive in any nested field.
    content = redact(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    run_id = report.get("run_id")
    if run_id:
        name = f"quality_report_{run_id}.json"
    else:
        name = f"quality_report_not_started_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path = reports_dir / name
    _atomic_write(path, content)
    _atomic_write(reports_dir / LATEST_NAME, content)
    return path
