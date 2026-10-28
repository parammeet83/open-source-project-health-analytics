from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from oss_health.config import GitHubSettings, Settings
from oss_health.redaction import clear_registered_secrets


class FakeTime:
    """Injectable clock/sleep: sleeping advances the clock, nothing really waits."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def gh_settings() -> GitHubSettings:
    return GitHubSettings(max_retries=3, per_page=2, max_pages_per_series=50)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    from dataclasses import replace

    from oss_health.config import OutputSettings

    base = Settings(
        repositories=("acme/alpha", "acme/beta"),
        github=GitHubSettings(per_page=2, max_pages_per_series=50, max_retries=2),
        output=OutputSettings(reports_dir=str(tmp_path / "reports"), write_log_file=False),
    )
    return replace(base, github_token=None, database_url=None)


@pytest.fixture(autouse=True)
def _reset_secrets():
    clear_registered_secrets()
    yield
    clear_registered_secrets()
