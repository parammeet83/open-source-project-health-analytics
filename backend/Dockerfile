# Read-only analytics API for Google Cloud Run.
# Build context: backend/ (see .github/workflows/deploy-cloud-run.yml and scripts/deploy_cloud_run.ps1)

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
# Only the API extra: no pandas/Pandera/Alembic in the runtime image.
RUN python -m venv /opt/venv && /opt/venv/bin/pip install ".[api]"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
USER 10001
EXPOSE 8080
# Cloud Run sets PORT; bind to all interfaces. Proxy headers are trusted because Cloud Run's front
# end is the only ingress. One worker per instance; scale out with Cloud Run instances instead.
CMD ["sh", "-c", "exec uvicorn oss_health.api.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30 --no-server-header --no-access-log"]
