"""Typed exception hierarchy. Scope rules are documented in docs/TRD.md §8."""

from __future__ import annotations


class OssHealthError(Exception):
    """Base class for all project errors."""


class ConfigError(OssHealthError):
    """Configuration is invalid; the run cannot start (exit 3)."""


class PreconditionError(OssHealthError):
    """A precondition failed (database unreachable, lock held); the run cannot start (exit 3)."""


class GitHubError(OssHealthError):
    """Base class for GitHub API failures. Never interpreted as 'no data'."""


class GitHubAuthError(GitHubError):
    """401 from GitHub. Run-fatal."""


class RateLimitExceededError(GitHubError):
    """Rate limit wait would exceed the configured maximum, or rate-limit retries ran out."""


class GitHubNotFoundError(GitHubError):
    """404/410: repository does not exist or is not visible."""


class GitHubClientError(GitHubError):
    """Other 4xx responses (non-retryable)."""


class GitHubTransientError(GitHubError):
    """5xx, timeouts or connection errors after all retries were exhausted."""


class GitHubResponseError(GitHubError):
    """Malformed JSON or an unexpected payload shape."""


class ValidationStructureError(OssHealthError):
    """The data frame cannot be validated row by row (for example a missing column)."""


class LoadError(OssHealthError):
    """Loading a repository failed; its transaction was rolled back."""


class UnsafeDatabaseTargetError(OssHealthError):
    """A destructive test operation was pointed at a database that is not a dedicated test DB."""
