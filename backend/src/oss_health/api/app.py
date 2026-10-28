"""ASGI entry point: ``uvicorn oss_health.api.app:app``."""

from oss_health.api.main import create_app

app = create_app()
