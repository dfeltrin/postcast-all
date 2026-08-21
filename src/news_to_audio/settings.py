"""Small persistent settings store for the local UI."""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "Overcast"
SETTINGS_FILE = Path("state/settings.json")


def load_output_dir(settings_file: Path = SETTINGS_FILE) -> Path:
    try:
        payload = json.loads(settings_file.read_text(encoding="utf-8"))
        value = payload.get("output_dir")
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser()
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_OUTPUT_DIR


def save_output_dir(output_dir: Path, settings_file: Path = SETTINGS_FILE) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps({"output_dir": str(output_dir.expanduser())}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
