"""Pandera row-level validation with quarantine of failing rows (docs/TRD.md §6)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

from oss_health.errors import ValidationStructureError

UTC_DT = pd.DatetimeTZDtype("ns", "UTC")
STATES = ["open", "closed"]
KNOWN_STATE_REASONS = {"completed", "not_planned", "reopened", "duplicate"}


def _text(nullable: bool = True, min_length: int | None = None) -> pa.Column:
    checks = [pa.Check.str_length(min_value=min_length)] if min_length else []
    return pa.Column(pd.StringDtype(), nullable=nullable, checks=checks)


def _positive_int(nullable: bool = False) -> pa.Column:
    return pa.Column("Int64", nullable=nullable, checks=pa.Check.gt(0))


def _non_negative_int(nullable: bool = False) -> pa.Column:
    return pa.Column("Int64", nullable=nullable, checks=pa.Check.ge(0))


def _ordered(later: str, earlier: str, name: str) -> pa.Check:
    def check(df: pd.DataFrame) -> pd.Series:
        return df[later].isna() | df[earlier].isna() | (df[later] >= df[earlier])

    return pa.Check(check, name=name, error=f"{later} must be on or after {earlier}")


def _belongs_to(repository_id: int) -> pa.Check:
    def check(df: pd.DataFrame) -> pd.Series:
        return df["repository_id"].isna() | (df["repository_id"] == repository_id)

    return pa.Check(check, name="repository_consistency", error="row belongs to another repository")


def repository_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "repository_id": _positive_int(),
            "node_id": _text(False, 1),
            "full_name": _text(False, 3),
            "owner_login": _text(False, 1),
            "repo_name": _text(False, 1),
            "html_url": _text(False, 1),
            "is_fork": pa.Column("bool"),
            "is_archived": pa.Column("bool"),
            "created_at": pa.Column(UTC_DT, nullable=False),
            "updated_at": pa.Column(UTC_DT, nullable=False),
            "pushed_at": pa.Column(UTC_DT, nullable=True),
        },
        checks=[_ordered("updated_at", "created_at", "updated_after_created")],
        strict=False,
        coerce=False,
    )


def snapshot_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "repository_id": _positive_int(),
            "captured_at": pa.Column(UTC_DT, nullable=False),
            "stargazers_count": _non_negative_int(),
            "forks_count": _non_negative_int(),
            "subscribers_count": _non_negative_int(nullable=True),
            "open_issues_and_prs_count": _non_negative_int(),
            "size_kb": _non_negative_int(nullable=True),
        },
        strict=False,
        coerce=False,
    )


def issue_schema(repository_id: int) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "issue_id": _positive_int(),
            "node_id": _text(False, 1),
            "repository_id": _positive_int(),
            "issue_number": _positive_int(),
            "title": _text(False),
            "state": pa.Column(pd.StringDtype(), nullable=False, checks=pa.Check.isin(STATES)),
            "is_bot_author": pa.Column("bool"),
            "assignee_count": _non_negative_int(),
            "comments_count": _non_negative_int(),
            "html_url": _text(False, 1),
            "created_at": pa.Column(UTC_DT, nullable=False),
            "updated_at": pa.Column(UTC_DT, nullable=False),
            "closed_at": pa.Column(UTC_DT, nullable=True),
            "is_pull_request": pa.Column(
                "bool",
                checks=pa.Check.eq(False, error="pull requests must not be loaded as issues"),
            ),
        },
        checks=[
            _ordered("updated_at", "created_at", "updated_after_created"),
            _ordered("closed_at", "created_at", "closed_after_created"),
            _belongs_to(repository_id),
        ],
        strict=False,
        coerce=False,
    )


def _merged_valid(df: pd.DataFrame) -> pd.Series:
    merged = df["merged_at"]
    ok_order = merged.isna() | df["created_at"].isna() | (merged >= df["created_at"])
    ok_state = merged.isna() | (df["state"] == "closed").fillna(False)
    return ok_order & ok_state


def pull_request_schema(repository_id: int) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "pull_request_id": _positive_int(),
            "node_id": _text(False, 1),
            "repository_id": _positive_int(),
            "pr_number": _positive_int(),
            "title": _text(False),
            "state": pa.Column(pd.StringDtype(), nullable=False, checks=pa.Check.isin(STATES)),
            "is_draft": pa.Column("bool"),
            "is_bot_author": pa.Column("bool"),
            "requested_reviewer_count": _non_negative_int(),
            "html_url": _text(False, 1),
            "created_at": pa.Column(UTC_DT, nullable=False),
            "updated_at": pa.Column(UTC_DT, nullable=False),
            "closed_at": pa.Column(UTC_DT, nullable=True),
            "merged_at": pa.Column(UTC_DT, nullable=True),
        },
        checks=[
            _ordered("updated_at", "created_at", "updated_after_created"),
            _ordered("closed_at", "created_at", "closed_after_created"),
            pa.Check(
                _merged_valid,
                name="merged_requires_closed",
                error="merged_at requires state 'closed' and merged_at >= created_at",
            ),
            _belongs_to(repository_id),
        ],
        strict=False,
        coerce=False,
    )


@dataclass
class ValidationOutcome:
    entity: str
    valid: pd.DataFrame
    evaluated: int
    rejected_rows: int
    rejections: list[dict[str, Any]] = field(default_factory=list)
    failures_by_check: dict[str, int] = field(default_factory=dict)

    @property
    def rejection_rate(self) -> float:
        return self.rejected_rows / self.evaluated if self.evaluated else 0.0


def json_safe(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp | datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _row_payload(row: pd.Series) -> dict[str, Any]:
    payload = {k: json_safe(v) for k, v in row.items()}
    text = json.dumps(payload, default=str)
    return payload if len(text) <= 20_000 else {"truncated": True}


def _reason(check: str, column: str | None, failure_case: Any) -> str:
    target = column or "row"
    if check == "not_nullable":
        return f"{target} is required but missing"
    return f"{target} failed {check} (value: {str(json_safe(failure_case))[:120]})"


def frame_checks_reason(schema: pa.DataFrameSchema, check_name: str) -> str:
    for check in schema.checks:
        if str(check.name) == check_name and check.error:
            return str(check.error)
    return f"row failed {check_name}"


def validate_frame(
    frame: pd.DataFrame,
    schema: pa.DataFrameSchema,
    *,
    entity: str,
    repository_full_name: str,
    id_column: str,
    number_column: str | None = None,
) -> ValidationOutcome:
    """Split ``frame`` into valid rows and quarantined rejections (one record per failed check)."""
    evaluated = len(frame)
    try:
        schema.validate(frame, lazy=True)
        return ValidationOutcome(entity, frame, evaluated, 0)
    except SchemaErrors as errors:
        cases = errors.failure_cases

    if cases["index"].isna().any():
        structural = cases[cases["index"].isna()]
        details = "; ".join(f"{r.column}: {r.check}" for r in structural.itertuples(index=False))[
            :500
        ]
        raise ValidationStructureError(f"{entity} frame failed structural validation: {details}")

    # Frame-wide checks are reported once per column with the error text as the check label;
    # map them back to their stable names and collapse to one failure per (row, check).
    frame_checks = {str(c.error): str(c.name) for c in schema.checks if c.error}
    rejections: list[dict[str, Any]] = []
    failures_by_check: dict[str, int] = {}
    bad_index: set[Any] = set()
    seen: set[tuple[Any, str, str | None]] = set()
    for case in cases.itertuples(index=False):
        idx = case.index
        if idx not in frame.index:
            continue
        label = str(case.check)
        is_frame_check = case.schema_context == "DataFrameSchema" or label in frame_checks
        check_name = frame_checks.get(label, label)
        column = None if is_frame_check else (case.column if isinstance(case.column, str) else None)
        key = (idx, check_name, column)
        if key in seen:
            continue
        seen.add(key)
        bad_index.add(idx)
        row = frame.loc[idx]
        failures_by_check[check_name] = failures_by_check.get(check_name, 0) + 1
        source_id = row.get(id_column)
        number = row.get(number_column) if number_column else None
        rejections.append(
            {
                "entity": entity,
                "repository_full_name": repository_full_name,
                "source_id": None if pd.isna(source_id) else int(source_id),
                "source_number": None if number is None or pd.isna(number) else int(number),
                "check_name": check_name[:200],
                "failure_reason": (
                    frame_checks_reason(schema, check_name)
                    if is_frame_check
                    else _reason(check_name, column, case.failure_case)
                )[:500],
                "failed_column": column,
                "failed_value": None if column is None else str(json_safe(case.failure_case))[:500],
                "record_payload": _row_payload(row),
            }
        )
    valid = frame.drop(index=list(bad_index))
    return ValidationOutcome(
        entity, valid, evaluated, len(bad_index), rejections, failures_by_check
    )


def unknown_state_reasons(frame: pd.DataFrame) -> dict[str, int]:
    if "state_reason" not in frame or frame.empty:
        return {}
    values = frame["state_reason"].dropna()
    unknown = values[~values.isin(KNOWN_STATE_REASONS)]
    return {str(k): int(v) for k, v in unknown.value_counts().items()}


def duplicate_keys(frame: pd.DataFrame, key: str) -> int:
    if frame.empty:
        return 0
    keyed = frame[key].dropna()
    return int(keyed.duplicated().sum())
