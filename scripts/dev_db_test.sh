#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

docker compose up -d db

export DB_DSN="postgresql://postgres:postgres@127.0.0.1:5432/postgres"
export DB_AUTO_MIGRATE=0
python -m src.migrate
python -m src.db_smoke

