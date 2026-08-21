from __future__ import annotations

import argparse
from pathlib import Path

from .notebooklm import NotebookLMError, download_ready_audios


def main() -> None:
    parser = argparse.ArgumentParser(description="Scarica gli Audio Overview pronti dai notebook esistenti")
    parser.add_argument("--output-dir", type=Path, default=Path("AudioOverview"))
    parser.add_argument("--profile-dir", type=Path, default=Path("state/browser-profile"))
    arguments = parser.parse_args()
    try:
        files, skipped = download_ready_audios(arguments.output_dir, arguments.profile_dir)
    except (NotebookLMError, OSError) as exc:
        raise SystemExit(f"Errore: {exc}") from exc
    print(f"Scaricati: {len(files)}. Notebook senza audio pronto: {skipped}.")
    for file in files:
        print(file)
