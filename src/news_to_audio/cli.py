"""Interactive, slash-command terminal interface for Podcast All."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from time import sleep

from .extract import SourceAccessError, extract_url
from .notebooklm import (
    NotebookLMDailyLimitError,
    NotebookLMError,
    delete_done_notebooks,
    download_ready_audios,
    normalize_title,
    process_url_to_completion,
    verify_audio_overviews,
)
from .settings import load_output_dir, save_output_dir
from .url_list import REJECTED_URL_LIST_FILE, URL_LIST_FILE, active_urls, add_url, count_urls, discard_url, remove_url


PROFILE_DIR = Path("state/browser-profile")
MAX_INACCESSIBLE_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _print(message: str = "") -> None:
    print(message, flush=True)


def _process_one(url: str, output_dir: Path) -> str:
    source = extract_url(url)
    title = normalize_title(source.title)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_file = process_url_to_completion(
        title, url, source.text, output_dir, PROFILE_DIR, "Italian", None, on_status=_print
    )
    required = (audio_file, audio_file.with_suffix(".txt"), audio_file.with_suffix(".md"))
    if not audio_file.is_file() or audio_file.stat().st_size == 0 or not all(path.is_file() for path in required[1:]):
        raise OSError(f"File finali incompleti: l’URL resta nella lista ({url})")
    if not remove_url(url):
        raise OSError(f"Audio e file associati creati, ma l’URL non è stato rimosso dalla lista: {url}")
    return title


def _sync(output_dir: Path) -> None:
    """Synchronize NotebookLM, then retry pending URLs until completed or limited."""
    verification = (0, 0, 0, 0, False)
    downloaded: tuple[list[Path], int] = ([], 0)
    deleted = 0
    try:
        _print("\nVerifica dei notebook esistenti…")
        verification = verify_audio_overviews(PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nElaborazione della lista interrotta.")
        return
    except NotebookLMError as exc:
        _print(f"Verifica non disponibile, proseguo con la lista: {exc}")
    if verification[4]:
        _print("\nLimite giornaliero raggiunto. Elaborazione della lista interrotta.")
        return

    try:
        _print("\nDownload degli Audio Overview già pronti…")
        downloaded = download_ready_audios(output_dir, PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nElaborazione della lista interrotta.")
        return
    except NotebookLMError as exc:
        _print(f"Nessun download completato, proseguo con la lista: {exc}")

    try:
        _print("\nEliminazione dei notebook [DONE] già salvati…")
        deleted = delete_done_notebooks(PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nElaborazione della lista interrotta.")
        return
    except NotebookLMError as exc:
        _print(f"Eliminazione non completata, proseguo con la lista: {exc}")

    completed: list[str] = []
    failed: dict[str, str] = {}
    inaccessible_attempts: dict[str, int] = {}
    automatically_rejected: list[tuple[str, str]] = []
    daily_limit = False
    cycle = 0
    while (urls := active_urls()):
        cycle += 1
        _print(f"\nPassaggio {cycle}: {len(urls)} URL da elaborare.")
        for position, url in enumerate(urls, start=1):
            _print(f"\n[{position}/{len(urls)}] {url}")
            try:
                completed.append(_process_one(url, output_dir))
                failed.pop(url, None)
            except NotebookLMDailyLimitError as exc:
                daily_limit = True
                _print(f"{exc}\nElaborazione della lista interrotta.")
                break
            except SourceAccessError as exc:
                inaccessible_attempts[url] = inaccessible_attempts.get(url, 0) + 1
                attempts = inaccessible_attempts[url]
                if attempts >= MAX_INACCESSIBLE_RETRIES and discard_url(url):
                    automatically_rejected.append((url, str(exc)))
                    failed.pop(url, None)
                    _print(f"Pagina non accessibile dopo {attempts} tentativi: spostata negli scartati.")
                else:
                    failed[url] = str(exc)
                    _print(f"Pagina non accessibile ({attempts}/{MAX_INACCESSIBLE_RETRIES}): resta in lista.")
            except Exception as exc:
                failed[url] = str(exc)
                _print(f"Errore: {exc}\nURL mantenuto in lista.")
        if daily_limit or not active_urls():
            break
        _print(f"\nPassaggio completato. Nuovo tentativo tra {RETRY_DELAY_SECONDS} secondi…")
        sleep(RETRY_DELAY_SECONDS)

    existing, maps_deleted, audio_started, verify_failed, _ = verification
    files, skipped = downloaded
    _print("\n── Riepilogo sincronizzazione ──")
    _print(f"Audio precedenti scaricati: {len(files)} · Notebook [DONE] eliminati: {deleted}")
    _print(f"Audio già presenti: {existing} · Mappe rimosse: {maps_deleted} · Audio avviati: {audio_started}")
    _print(f"URL completati: {len(completed)} · URL ancora in lista: {count_urls(URL_LIST_FILE)}")
    _print(f"URL scartati automaticamente: {len(automatically_rejected)}")
    if skipped or verify_failed:
        _print(f"Notebook da riesaminare: {skipped + verify_failed}")
    if daily_limit:
        _print("Limite giornaliero raggiunto: gli URL rimanenti restano in lista.")


def _show_urls(path: Path, heading: str) -> None:
    urls = active_urls(path)
    _print(f"\n{heading} ({len(urls)})")
    for number, url in enumerate(urls, start=1):
        _print(f"  {number}. {url}")


def _help() -> None:
    _print("""
Comandi disponibili:
  /add, /a <url>       aggiunge un URL alla coda (i duplicati sono ignorati)
  /list, /l            mostra gli URL in attesa
  /rejected, /r        mostra gli URL scartati automaticamente
  /sync, /s            verifica, scarica, archivia e completa la coda
  /output, /o [cartella] mostra o imposta la cartella di output
  /help, /h            mostra questo aiuto
  /quit, /q            chiude l'applicazione
""".strip())


def main() -> None:
    output_dir = load_output_dir()
    _print("Podcast All CLI")
    _print("Digita /help per i comandi. Gli URL restano in lista finché MP3, TXT e MD non sono completi.")
    while True:
        try:
            line = input("\npodcast-all › ").strip()
        except (EOFError, KeyboardInterrupt):
            _print("\nArrivederci.")
            return
        if not line:
            continue
        if not line.startswith("/"):
            if line.startswith(("http://", "https://")):
                if add_url(line):
                    _print("URL aggiunto alla lista.")
                else:
                    _print("URL già presente nella lista.")
            else:
                _print("Usa un comando che inizia con / oppure digita direttamente un URL. Digita /help.")
            continue
        try:
            parts = shlex.split(line[1:])
        except ValueError as exc:
            _print(f"Comando non valido: {exc}")
            continue
        command, *arguments = parts
        command = command.lower()
        command = {
            "a": "add",
            "l": "list",
            "r": "rejected",
            "s": "sync",
            "o": "output",
            "h": "help",
            "q": "quit",
        }.get(command, command)
        if command in {"quit", "exit", "q"}:
            _print("Arrivederci.")
            return
        if command in {"help", "?"}:
            _help()
        elif command == "add":
            if len(arguments) != 1 or not arguments[0].startswith(("http://", "https://")):
                _print("Uso: /add https://esempio.it/articolo")
            elif add_url(arguments[0]):
                _print("URL aggiunto alla lista.")
            else:
                _print("URL già presente nella lista.")
        elif command == "list":
            _show_urls(URL_LIST_FILE, "URL in lista")
        elif command in {"rejected", "scartati"}:
            _show_urls(REJECTED_URL_LIST_FILE, "URL scartati")
        elif command == "output":
            if not arguments:
                _print(f"Cartella output: {output_dir}")
            elif len(arguments) == 1:
                output_dir = Path(arguments[0]).expanduser()
                save_output_dir(output_dir)
                _print(f"Cartella output impostata: {output_dir}")
            else:
                _print("Uso: /output [/percorso/cartella]")
        elif command == "sync":
            _sync(output_dir)
        else:
            _print("Comando sconosciuto. Digita /help.")


if __name__ == "__main__":
    main()
