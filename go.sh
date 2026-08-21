#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Ambiente non trovato. Esegui prima: python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

exec "$VENV_PYTHON" -B -m news_to_audio.cli
