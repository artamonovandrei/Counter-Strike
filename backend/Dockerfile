# path: backend/Dockerfile
# Build context is the repository root, because the backend reads assets/ at runtime.
#   docker build -f backend/Dockerfile -t webstrike-backend .

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first: this layer only rebuilds when requirements.txt changes, which keeps
# iteration on game code down to a couple of seconds.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY assets ./assets
COPY shared ./shared

# The app resolves assets relative to the repo root two levels above app/config.py, so
# tell it explicitly instead of relying on the layout surviving the copy.
ENV ASSETS_DIR=/srv/assets

# Run unprivileged. Nothing here writes to disk at runtime.
RUN useradd --system --uid 10001 --no-create-home webstrike \
    && chown -R webstrike:webstrike /srv
USER webstrike

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

# One worker on purpose: a room lives in the memory of a single process, so a second
# worker would create rooms the first one cannot see. Scale with Redis + separate
# containers, not with --workers. See the scaling notes in the README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
