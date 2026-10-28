"""ASGI entry point: ``uvicorn oss_health.api.app:app``."""

from oss_health.api.main import create_app
from oss_health.config import load_env_files

# Local development only: picks up API_DATABASE_URL from the git-ignored env files. Vercel does not
# install python-dotenv, so this is a no-op there; the value comes from Vercel's encrypted
# project environment variables.
load_env_files()

app = create_app()
