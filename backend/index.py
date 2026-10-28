"""Vercel entrypoint for the read-only analytics API.

Vercel's Python runtime discovers a FastAPI instance named ``app`` in ``index.py`` at the project
root (the Vercel project's Root Directory is ``backend``). The application itself lives in the
``src/``-layout package ``oss_health``; this shim makes it importable whether or not the build
installs the project, then re-exports the same app used locally
(``uvicorn oss_health.api.app:app``).
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from oss_health.api.app import app  # noqa: E402

__all__ = ["app"]
