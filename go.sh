#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
  SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
  SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
  # I link relativi si risolvono rispetto alla directory del link.
  [[ "$SCRIPT_PATH" = /* ]] || SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
PROJECT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
  printf 'Ambiente non trovato. Esegui prima:\n  cd %q && python3 -m venv .venv && .venv/bin/pip install -e .\n' "$PROJECT_DIR" >&2
  exit 1
fi

exec "$VENV_PYTHON" -B -m news_to_audio.cli
