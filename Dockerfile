FROM ghcr.io/astral-sh/uv:0.10.10@sha256:cbe0a44ba994e327b8fe7ed72beef1aaa7d2c4c795fd406d1dbf328bacb2f1c5 AS uv

FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    MEPPP_ENV=production \
    MEPPP_DATA_DIR=/data

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system --gid 10001 meppp \
    && useradd --system --uid 10001 --gid meppp --home-dir /app meppp \
    && mkdir -p /app /data \
    && chown -R meppp:meppp /app /data

WORKDIR /app

COPY --chown=meppp:meppp pyproject.toml uv.lock README.md ./
COPY --chown=meppp:meppp src ./src
COPY --chown=meppp:meppp manage.py ./
COPY --chown=meppp:meppp --chmod=755 docker/entrypoint.sh ./docker/entrypoint.sh
COPY --chown=meppp:meppp docker/healthcheck.py ./docker/healthcheck.py

RUN uv sync --locked --no-dev --no-editable

USER meppp

EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["gunicorn", "meppp.config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
