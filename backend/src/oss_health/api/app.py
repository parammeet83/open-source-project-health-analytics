"""ASGI entry point: ``uvicorn oss_health.api.app:app``."""

from oss_health.api.main import create_app
from oss_health.config import load_env_files

# Local development only: picks up API_DATABASE_URL from the git-ignored env files. The Cloud Run
# image has no python-dotenv, so this is a no-op there and Secret Manager supplies the value.
load_env_files()

app = create_app()
