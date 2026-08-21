"""Manual end-to-end smoke test for the NotebookLM browser flow.

Run explicitly with: .venv/bin/python -B scripts/e2e_smoke.py
"""
from pathlib import Path

from news_to_audio.extract import extract_url
from news_to_audio.notebooklm import start_audio_generation

URL = "https://futurism.com/artificial-intelligence/ai-companies-destroying-rare-books"

source = extract_url(URL)
print(f"ESTRATTO: {len(source.text)} caratteri", flush=True)
start_audio_generation(
    "Test E2E Futurism",
    URL,
    source.text,
    Path("AudioOverview"),
    Path("state/browser-profile"),
    "Italian",
    "Crea una breve panoramica in italiano.",
    on_status=lambda value: print(f"STATO: {value}", flush=True),
)
print("GENERAZIONE AVVIATA", flush=True)
