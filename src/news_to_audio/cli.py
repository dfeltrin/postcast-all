"""Interactive, slash-command terminal interface for Podcast All."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from time import sleep
from urllib.parse import unquote, urlsplit

from .extract import SourceAccessError, extract_url
from .notebooklm import (
    NotebookLMDailyLimitError,
    NotebookLMError,
    delete_done_notebooks,
    download_ready_audios,
    normalize_title,
    process_file_to_completion,
    process_url_to_completion,
    verify_audio_overviews,
)
from .settings import load_output_dir, save_output_dir
from .url_list import REJECTED_URL_LIST_FILE, URL_LIST_FILE, active_urls, add_file, add_url, count_urls, discard_url, remove_all, remove_url


PROFILE_DIR = Path("state/browser-profile")
MAX_INACCESSIBLE_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _print(message: str = "") -> None:
    print(message, flush=True)


def _process_one(url: str, output_dir: Path) -> str:
    if urlsplit(url).scheme == "file":
        file_path = Path(unquote(urlsplit(url).path))
        title = normalize_title(file_path.stem)
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_file = process_file_to_completion(file_path, output_dir, PROFILE_DIR, "Italian", None, on_status=_print)
        required = (audio_file, audio_file.with_suffix(".txt"))
        if file_path.suffix.lower() == ".pdf":
            required += (audio_file.with_suffix(".pdf"),)
        if not audio_file.is_file() or audio_file.stat().st_size == 0 or not all(path.is_file() for path in required[1:]):
            raise OSError(f"Output files are incomplete: the file remains in the queue ({file_path})")
        if not remove_url(url):
            raise OSError(f"Audio and sidecar files were created, but the file was not removed from the queue: {file_path}")
        return title
    source = extract_url(url)
    title = normalize_title(source.title)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_file = process_url_to_completion(
        title, url, source.text, output_dir, PROFILE_DIR, "Italian", None, on_status=_print
    )
    required = (audio_file, audio_file.with_suffix(".txt"), audio_file.with_suffix(".md"))
    if not audio_file.is_file() or audio_file.stat().st_size == 0 or not all(path.is_file() for path in required[1:]):
        raise OSError(f"Output files are incomplete: the URL remains in the queue ({url})")
    if not remove_url(url):
        raise OSError(f"Audio and sidecar files were created, but the URL was not removed from the queue: {url}")
    return title


def _sync(output_dir: Path) -> None:
    """Synchronize NotebookLM, then retry pending URLs until completed or limited."""
    verification = (0, 0, 0, 0, False)
    downloaded: tuple[list[Path], int] = ([], 0)
    deleted = 0
    try:
        _print("\nChecking existing notebooks…")
        verification = verify_audio_overviews(PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nQueue processing stopped.")
        return
    except NotebookLMError as exc:
        _print(f"Checking is unavailable; continuing with the queue: {exc}")
    if verification[4]:
        _print("\nDaily limit reached. Queue processing stopped.")
        return

    try:
        _print("\nDownloading ready Audio Overviews…")
        downloaded = download_ready_audios(output_dir, PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nQueue processing stopped.")
        return
    except NotebookLMError as exc:
        _print(f"No download completed; continuing with the queue: {exc}")

    try:
        _print("\nDeleting saved [DONE] notebooks…")
        deleted = delete_done_notebooks(PROFILE_DIR, on_status=_print)
    except NotebookLMDailyLimitError as exc:
        _print(f"\n{exc}\nQueue processing stopped.")
        return
    except NotebookLMError as exc:
        _print(f"Deletion was not completed; continuing with the queue: {exc}")

    completed: list[str] = []
    failed: dict[str, str] = {}
    inaccessible_attempts: dict[str, int] = {}
    automatically_rejected: list[tuple[str, str]] = []
    daily_limit = False
    file_needs_attention = False
    cycle = 0
    while (urls := active_urls()):
        cycle += 1
        _print(f"\nPass {cycle}: {len(urls)} items to process.")
        for position, url in enumerate(urls, start=1):
            label = Path(unquote(urlsplit(url).path)).name if urlsplit(url).scheme == "file" else url
            _print(f"\n[{position}/{len(urls)}] {label}")
            try:
                completed.append(_process_one(url, output_dir))
                failed.pop(url, None)
            except NotebookLMDailyLimitError as exc:
                daily_limit = True
                _print(f"{exc}\nQueue processing stopped.")
                break
            except SourceAccessError as exc:
                inaccessible_attempts[url] = inaccessible_attempts.get(url, 0) + 1
                attempts = inaccessible_attempts[url]
                if attempts >= MAX_INACCESSIBLE_RETRIES and discard_url(url):
                    automatically_rejected.append((url, str(exc)))
                    failed.pop(url, None)
                    _print(f"Page inaccessible after {attempts} attempts: moved to rejected items.")
                else:
                    failed[url] = str(exc)
                    _print(f"Page inaccessible ({attempts}/{MAX_INACCESSIBLE_RETRIES}): remains in the queue.")
            except Exception as exc:
                failed[url] = str(exc)
                if urlsplit(url).scheme == "file":
                    file_needs_attention = True
                    _print(f"Error: {exc}\nFile retained in the queue. Synchronization paused: run /sync again after checking it.")
                    break
                _print(f"Error: {exc}\nURL retained in the queue.")
        if daily_limit or file_needs_attention or not active_urls():
            break
        _print(f"\nPass complete. Retrying in {RETRY_DELAY_SECONDS} seconds…")
        sleep(RETRY_DELAY_SECONDS)

    existing, maps_deleted, audio_started, verify_failed, _ = verification
    files, skipped = downloaded
    _print("\n── Synchronization summary ──")
    _print(f"Previous audio downloaded: {len(files)} · [DONE] notebooks deleted: {deleted}")
    _print(f"Audio already present: {existing} · Mind maps removed: {maps_deleted} · Audio started: {audio_started}")
    _print(f"Items completed: {len(completed)} · Items still queued: {count_urls(URL_LIST_FILE)}")
    _print(f"URLs automatically rejected: {len(automatically_rejected)}")
    if skipped or verify_failed:
        _print(f"Notebooks to review: {skipped + verify_failed}")
    if daily_limit:
        _print("Daily limit reached: remaining URLs stay in the queue.")


def _show_urls(path: Path, heading: str) -> None:
    urls = active_urls(path)
    _print(f"\n{heading} ({len(urls)})")
    for number, url in enumerate(urls, start=1):
        if urlsplit(url).scheme == "file":
            _print(f"  {number}. [file] {Path(unquote(urlsplit(url).path))}")
        else:
            _print(f"  {number}. {url}")


def _pick_file() -> Path | None:
    """Wait for the picker process to exit before returning to the CLI."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "news_to_audio.file_picker"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        selection = json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        _print(f"Unable to open the file picker: {exc.stderr.strip() or exc}")
        return None
    except Exception as exc:
        _print(f"Unable to open the file picker: {exc}")
        return None
    return Path(selection) if selection else None


def _help() -> None:
    _print("""
Available commands:
  /add, /a <url>       adds a URL to the queue (duplicates are ignored)
  /addfile             opens the picker and adds a local file to the queue
  /list, /l            shows pending URLs and files
  /removeall, /r       removes every item from the queue
  /rejected            shows automatically rejected URLs
  /sync, /s            checks, downloads, archives, and completes the queue
  /output, /o [directory] shows or sets the output directory
  /help, /h            shows this help
  /quit, /q            closes the application
""".strip())


def main() -> None:
    output_dir = load_output_dir()
    _print("Podcast All CLI")
    _print("Type /help for commands. Items remain queued until all required output files are complete.")
    while True:
        try:
            line = input("\npodcast-all › ").strip()
        except (EOFError, KeyboardInterrupt):
            _print("\nGoodbye.")
            return
        if not line:
            continue
        if not line.startswith("/"):
            if line.startswith(("http://", "https://")):
                if add_url(line):
                    _print("URL added to the queue.")
                else:
                    _print("URL is already in the queue.")
            else:
                _print("Use a command beginning with / or enter a URL directly. Type /help.")
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
            "af": "addfile",
            "l": "list",
            "r": "removeall",
            "s": "sync",
            "o": "output",
            "h": "help",
            "q": "quit",
        }.get(command, command)
        if command in {"quit", "exit", "q"}:
            _print("Goodbye.")
            return
        if command in {"help", "?"}:
            _help()
        elif command == "add":
            if len(arguments) != 1 or not arguments[0].startswith(("http://", "https://")):
                _print("Usage: /add https://example.com/article")
            elif add_url(arguments[0]):
                _print("URL added to the queue.")
            else:
                _print("URL is already in the queue.")
        elif command == "addfile":
            if arguments:
                _print("Usage: /addfile")
                continue
            file_path = _pick_file()
            if file_path is None:
                _print("No file selected.")
            elif not file_path.is_file():
                _print(f"Invalid file: {file_path}")
            elif add_file(file_path):
                _print(f"File added to the queue: {file_path.name}")
            else:
                _print("File is already in the queue.")
        elif command == "list":
            _show_urls(URL_LIST_FILE, "Queued URLs")
        elif command == "removeall":
            if arguments:
                _print("Usage: /removeall")
            else:
                removed = remove_all(URL_LIST_FILE)
                _print(f"Queue cleared: removed {removed} items.")
        elif command in {"rejected", "scartati"}:
            _show_urls(REJECTED_URL_LIST_FILE, "Rejected URLs")
        elif command == "output":
            if not arguments:
                _print(f"Cartella output: {output_dir}")
            elif len(arguments) == 1:
                output_dir = Path(arguments[0]).expanduser()
                save_output_dir(output_dir)
                _print(f"Cartella output impostata: {output_dir}")
            else:
                _print("Usage: /output [/path/to/directory]")
        elif command == "sync":
            _sync(output_dir)
        else:
            _print("Unknown command. Type /help.")


if __name__ == "__main__":
    main()
