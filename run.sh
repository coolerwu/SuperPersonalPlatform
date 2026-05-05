#!/usr/bin/env bash
set -euo pipefail

HOST="${SUPER_PERSONAL_HOST:-0.0.0.0}"
PORT="${SUPER_PERSONAL_PORT:-8888}"
CONFIG_PATH="${SUPER_PERSONAL_CONFIG:-config.yaml}"

export SUPER_PERSONAL_CONFIG="$CONFIG_PATH"

exec python3 -m uvicorn server.infrastructure.fastapi_app:create_app \
  --factory \
  --host "$HOST" \
  --port "$PORT"
