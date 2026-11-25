#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
echo "Starting Westbound (YOW -> YVR Flight Concierge) on http://localhost:${PORT}"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
