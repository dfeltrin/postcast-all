"""A small, human-editable queue of URLs and local files waiting to be processed."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


URL_LIST_FILE = Path("state/url-list.txt")
REJECTED_URL_LIST_FILE = Path("state/rejected-urls.txt")


def canonical_url(url: str) -> str:
    """Normalize harmless URL variations for duplicate detection only."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def add_url(url: str, list_file: Path = URL_LIST_FILE) -> bool:
    """Append *url* once and return whether the list changed.

    The format intentionally stays plain text: one URL per line, with optional
    empty lines or comment lines beginning with ``#`` for manual maintenance.
    """
    list_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = {
            line.strip()
            for line in list_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError:
        existing = set()
    if canonical_url(url) in {canonical_url(item) for item in existing}:
        return False
    with list_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{url}\n")
    return True


def add_file(path: Path, list_file: Path = URL_LIST_FILE) -> bool:
    """Append a local file to the same FIFO queue used by URLs.

    File entries are stored as absolute ``file://`` URIs.  This keeps the queue
    plain text and preserves the order in which URLs and files were added.
    """
    return add_url(path.expanduser().resolve().as_uri(), list_file)


def first_url(list_file: Path = URL_LIST_FILE) -> str | None:
    """Return the first active URL, ignoring empty lines and comments."""
    try:
        lines = list_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return next((line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")), None)


def count_urls(list_file: Path = URL_LIST_FILE) -> int:
    """Count active queue entries without counting comments or blank lines."""
    try:
        lines = list_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(bool(line.strip() and not line.lstrip().startswith("#")) for line in lines)


def active_urls(list_file: Path = URL_LIST_FILE) -> list[str]:
    """Return a stable snapshot of the URLs currently awaiting processing."""
    try:
        lines = list_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def remove_url(url: str, list_file: Path = URL_LIST_FILE) -> bool:
    """Remove the first matching URL while preserving all manual annotations."""
    try:
        lines = list_file.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False
    removed = False
    kept: list[str] = []
    for line in lines:
        if not removed and line.strip() == url:
            removed = True
            continue
        kept.append(line)
    if removed:
        list_file.write_text("".join(kept), encoding="utf-8")
    return removed


def remove_all(list_file: Path = URL_LIST_FILE) -> int:
    """Remove every entry and comment from the queue, returning its prior size."""
    previous_count = count_urls(list_file)
    try:
        list_file.parent.mkdir(parents=True, exist_ok=True)
        list_file.write_text("", encoding="utf-8")
    except OSError:
        return 0
    return previous_count


def discard_url(
    url: str,
    list_file: Path = URL_LIST_FILE,
    rejected_file: Path = REJECTED_URL_LIST_FILE,
) -> bool:
    """Move one queued URL to the human-editable rejected list.

    The rejected copy is persisted first, so an I/O failure cannot make the URL
    disappear from both lists.  Duplicate rejected entries are avoided using the
    same canonical form as the active queue.
    """
    if url not in active_urls(list_file):
        return False
    add_url(url, rejected_file)
    return remove_url(url, list_file)
