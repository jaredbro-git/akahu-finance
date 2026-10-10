#!/usr/bin/env bash
# Start the dashboard at http://127.0.0.1:8000 (local only).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "No .env file yet: copy .env.example to .env and add your Akahu tokens." >&2
  exit 1
fi

exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
