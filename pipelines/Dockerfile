# The worker only: it runs `python -m pipelines`, nothing else in this stack
# is built locally. `uv` is copied from its own pinned image rather than
# installed with pip, so the Dockerfile does not need network access beyond
# the base images and the locked packages themselves.

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /uvx /usr/local/bin/

WORKDIR /app

# No uv cache in the image: it would only add the downloaded wheels a second time.
ENV UV_NO_CACHE=1

# Only the lock and the project metadata first, and the dependencies without
# the project itself, so a code-only change does not invalidate that layer.
# `--locked` refuses to silently update the lock; `--no-dev` leaves ruff,
# mypy and pytest out of the image that actually runs in production.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /app
USER worker

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["python", "-m", "pipelines"]
