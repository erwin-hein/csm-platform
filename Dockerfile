FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv

# Dependencies first so code edits don't re-install them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
EXPOSE 8000
# Migrate, seed if SEED_DEMO_DATA=true and the DB is empty, then serve — same script Render runs.
CMD ["./scripts/start.sh"]
