#!/usr/bin/env bash
# Render start command: migrate, optionally seed demo data (no-op if already seeded), serve.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/alembic upgrade head
if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  .venv/bin/python -m app.seed
fi
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
