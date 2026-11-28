# L'API, en image — construite par la stack Portainer deploy/ea.stack.yml.
# Voir docs/adr/0027.
#
# Tag et digest, comme les stacks : un rebuild ne change pas de base sans que
# ce fichier ait changé. Dependabot propose les mises à jour.

FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Les dépendances d'abord, le code ensuite : changer une ligne de src/ ne
# réinstalle pas tout. `--locked` refuse un uv.lock périmé, comme la CI.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# Même Python, même chemin (/usr/local/bin/python3.12) que l'image de build :
# le venv copié y pointe.
FROM python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

# Les rapports PDF (docs/adr/0062) : WeasyPrint charge Pango à l'exécution. La
# police est embarquée (DejaVu) pour que le rendu ne dépende d'aucune machine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home ea
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations

# XDG_CACHE_HOME : l'utilisateur `ea` n'a pas de HOME ; fontconfig y écrit son cache.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 XDG_CACHE_HOME=/tmp
USER ea
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]

# La chaîne Alembic est appliquée à chaque démarrage : idempotente, et une base
# neuve (celle de l'Infra) devient utilisable en démarrant le conteneur. Une
# migration qui échoue arrête le conteneur au lieu de servir une base à moitié
# migrée.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn ea.main:app --host 0.0.0.0 --port 8000"]
