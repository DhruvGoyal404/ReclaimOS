# Reproducible ReclaimOS image. Installs from the lockfile, never resolves at build.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Non-root: nothing here needs to write outside /app/data.
RUN useradd --create-home --uid 10001 reclaim \
    && mkdir -p /app/data && chown -R reclaim:reclaim /app
USER reclaim

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["reclaimos"]
CMD ["--help"]
