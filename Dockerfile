FROM python:3.12-slim AS builder
ARG INSTALL_DEV=true
ARG PIP_TRUSTED_HOSTS=""
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
RUN set -eu; \
    case "$INSTALL_DEV" in \
        true) lock=requirements-dev.txt ;; \
        false) lock=requirements.txt ;; \
        *) echo "INSTALL_DEV must be true or false" >&2; exit 1 ;; \
    esac; \
    for host in $PIP_TRUSTED_HOSTS; do set -- "$@" --trusted-host "$host"; done; \
    pip wheel --no-cache-dir --require-hashes "$@" --wheel-dir=/wheels -r "$lock"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home forseti \
    && chown forseti:forseti /app
RUN --mount=from=builder,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --no-deps /wheels/*.whl

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY app/ ./app/
COPY agents/ ./agents/
COPY scripts/ ./scripts/

USER 10001
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
