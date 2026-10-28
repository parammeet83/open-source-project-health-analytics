"""Idempotent PostgreSQL upserts with stale-update guards (docs/Backend_DB_Schema.md §5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Table, and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from oss_health.models import (
    extraction_watermarks,
    github_issues,
    pull_requests,
    repositories,
    repository_snapshots,
)
from oss_health.validation import json_safe

AUDIT_COLUMNS = {"etl_inserted_at", "etl_updated_at"}


@dataclass
class UpsertStats:
    attempted: int = 0
    inserted: int = 0
    updated: int = 0

    @property
    def unchanged(self) -> int:
        return self.attempted - self.inserted - self.updated

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
        }


def frame_records(frame: pd.DataFrame, columns: Iterable[str]) -> list[dict[str, Any]]:
    """Convert a typed frame into DB-ready Python records (pd.NA/NaT -> None)."""
    cols = [c for c in columns if c in frame.columns]
    records: list[dict[str, Any]] = []
    for row in frame[cols].itertuples(index=False, name=None):
        record: dict[str, Any] = {}
        for column, value in zip(cols, row, strict=True):
            if value is pd.NaT:
                record[column] = None
            elif isinstance(value, pd.Timestamp):
                record[column] = value.to_pydatetime()
            elif isinstance(value, list | date | datetime):
                record[column] = value
            else:
                record[column] = json_safe(value)
        records.append(record)
    return records


def _batches(rows: Sequence[dict[str, Any]], size: int) -> Iterable[Sequence[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _upsert(
    conn: Connection,
    table: Table,
    rows: Sequence[dict[str, Any]],
    *,
    conflict: list[str],
    guard_builder,
    batch_size: int,
    extra_set: dict[str, Any] | None = None,
) -> UpsertStats:
    stats = UpsertStats(attempted=len(rows))
    if not rows:
        return stats
    columns = list(rows[0].keys())
    for batch in _batches(rows, batch_size):
        stmt = pg_insert(table).values(list(batch))
        excluded = stmt.excluded
        set_ = {c: excluded[c] for c in columns if c not in conflict and c not in AUDIT_COLUMNS}
        if "etl_updated_at" in table.c:
            set_["etl_updated_at"] = func.now()
        if extra_set:
            set_.update(extra_set)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict, set_=set_, where=guard_builder(table, excluded, columns)
        ).returning(literal_column("(xmax = 0)").label("inserted"))
        for (was_inserted,) in conn.execute(stmt):
            if was_inserted:
                stats.inserted += 1
            else:
                stats.updated += 1
    return stats


def _distinct_guard(skip: set[str]):
    def build(table: Table, excluded, columns: list[str]):
        return or_(*[table.c[c].is_distinct_from(excluded[c]) for c in columns if c not in skip])

    return build


def _newer_guard(column: str, inclusive: bool):
    def build(table: Table, excluded, columns: list[str]):
        if inclusive:
            return excluded[column] >= table.c[column]
        return excluded[column] > table.c[column]

    return build


def upsert_repository(
    conn: Connection, frame: pd.DataFrame, run_id: uuid.UUID, batch_size: int
) -> UpsertStats:
    rows = frame_records(frame, [c.name for c in repositories.c if c.name not in AUDIT_COLUMNS])
    for row in rows:
        row["is_tracked"] = True
        row["etl_run_id"] = run_id
    return _upsert(
        conn,
        repositories,
        rows,
        conflict=["repository_id"],
        guard_builder=_distinct_guard({"repository_id", "etl_run_id"}),
        batch_size=batch_size,
    )


def upsert_snapshot(
    conn: Connection, frame: pd.DataFrame, run_id: uuid.UUID, batch_size: int
) -> UpsertStats:
    rows = frame_records(frame, [c.name for c in repository_snapshots.c])
    for row in rows:
        row["etl_run_id"] = run_id
    return _upsert(
        conn,
        repository_snapshots,
        rows,
        conflict=["repository_id", "snapshot_date"],
        guard_builder=_snapshot_guard,
        batch_size=batch_size,
    )


SNAPSHOT_METRICS = (
    "stargazers_count",
    "forks_count",
    "subscribers_count",
    "open_issues_and_prs_count",
    "size_kb",
)


def _snapshot_guard(table: Table, excluded, columns: list[str]):
    """A later same-day capture replaces the row only when a metric actually changed."""
    changed = or_(*[table.c[c].is_distinct_from(excluded[c]) for c in SNAPSHOT_METRICS])
    return and_(excluded.captured_at > table.c.captured_at, changed)


def upsert_issues(
    conn: Connection,
    frame: pd.DataFrame,
    run_id: uuid.UUID,
    batch_size: int,
    *,
    full_refresh: bool = False,
) -> UpsertStats:
    rows = frame_records(frame, [c.name for c in github_issues.c if c.name not in AUDIT_COLUMNS])
    for row in rows:
        row["etl_run_id"] = run_id
    return _upsert(
        conn,
        github_issues,
        rows,
        conflict=["issue_id"],
        guard_builder=_newer_guard("updated_at", inclusive=full_refresh),
        batch_size=batch_size,
    )


def upsert_pull_requests(
    conn: Connection,
    frame: pd.DataFrame,
    run_id: uuid.UUID,
    batch_size: int,
    *,
    full_refresh: bool = False,
) -> UpsertStats:
    rows = frame_records(frame, [c.name for c in pull_requests.c if c.name not in AUDIT_COLUMNS])
    for row in rows:
        row["etl_run_id"] = run_id
    return _upsert(
        conn,
        pull_requests,
        rows,
        conflict=["pull_request_id"],
        guard_builder=_newer_guard("updated_at", inclusive=full_refresh),
        batch_size=batch_size,
    )


def count_present(conn: Connection, table: Table, key: str, ids: Sequence[int]) -> int:
    """Number of the given primary keys present in ``table`` (load reconciliation)."""
    if not ids:
        return 0
    return int(
        conn.execute(
            select(func.count()).select_from(table).where(table.c[key].in_(list(ids)))
        ).scalar_one()
    )


def get_watermarks(conn: Connection, repository_id: int) -> dict[str, dict[str, datetime]]:
    rows = conn.execute(
        select(
            extraction_watermarks.c.resource,
            extraction_watermarks.c.high_watermark,
            extraction_watermarks.c.coverage_start_at,
        ).where(extraction_watermarks.c.repository_id == repository_id)
    )
    return {
        r.resource: {"high_watermark": r.high_watermark, "coverage_start_at": r.coverage_start_at}
        for r in rows
    }


def find_repository_id(conn: Connection, full_name: str) -> int | None:
    return conn.execute(
        select(repositories.c.repository_id).where(
            func.lower(repositories.c.full_name) == full_name.lower()
        )
    ).scalar_one_or_none()


def advance_watermark(
    conn: Connection,
    *,
    repository_id: int,
    resource: str,
    high_watermark: datetime,
    coverage_start_at: datetime,
    run_id: uuid.UUID,
) -> bool:
    """Move a watermark forward (never backwards). Coverage start only ever moves earlier."""
    t = extraction_watermarks
    stmt = pg_insert(t).values(
        repository_id=repository_id,
        resource=resource,
        high_watermark=high_watermark,
        coverage_start_at=min(coverage_start_at, high_watermark),
        last_successful_run_id=run_id,
    )
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=["repository_id", "resource"],
        set_={
            "high_watermark": func.greatest(t.c.high_watermark, ex.high_watermark),
            "coverage_start_at": func.least(t.c.coverage_start_at, ex.coverage_start_at),
            "last_successful_run_id": ex.last_successful_run_id,
            "updated_at": func.now(),
        },
        where=or_(
            ex.high_watermark > t.c.high_watermark,
            ex.coverage_start_at < t.c.coverage_start_at,
        ),
    ).returning(literal_column("1"))
    return conn.execute(stmt).first() is not None


def set_tracked_repositories(conn: Connection, tracked_ids: Sequence[int]) -> int:
    """Mark repositories removed from the configuration as untracked (history is kept)."""
    result = conn.execute(
        repositories.update()
        .where(
            and_(
                repositories.c.is_tracked.is_(True),
                repositories.c.repository_id.not_in(list(tracked_ids)),
            )
        )
        .values(is_tracked=False, etl_updated_at=func.now())
    )
    return result.rowcount or 0


def record_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {k: json_safe(v) for k, v in row.items()}
