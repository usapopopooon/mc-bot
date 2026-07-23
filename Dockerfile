FROM ghcr.io/astral-sh/uv:0.11.8 AS uv

FROM python:3.14.6-slim AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev

FROM dependencies AS test
COPY tests ./tests
RUN uv sync --locked \
    && uv run pytest \
    && uv run ruff check src tests \
    && uv run ruff format --check src tests \
    && touch /tmp/tests-passed

FROM python:3.14.6-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/home/mc-bot" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 1000 mc-bot \
    && useradd --uid 1000 --gid mc-bot --create-home mc-bot
WORKDIR /app
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=dependencies /app/src /app/src
COPY --from=test /tmp/tests-passed /tmp/tests-passed
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN mkdir -p /data \
    && chown mc-bot:mc-bot /data \
    && chmod 755 /usr/local/bin/docker-entrypoint.sh
HEALTHCHECK --interval=30s --timeout=3s --start-period=45s --retries=3 \
    CMD ["python", "-c", "import os,time,sys; p='/tmp/mc-bot-healthy'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<45 else 1)"]
ENTRYPOINT ["docker-entrypoint.sh"]
