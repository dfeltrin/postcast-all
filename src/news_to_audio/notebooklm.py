from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

TEXT_LIMIT = 300_000
GENERATION_TIMEOUT_MS = 12_000_000
GENERATION_POLL_MS = 30_000
MAX_CLEAN_RECREATION_ATTEMPTS = 2
FILE_UPLOAD_TIMEOUT_SECONDS = 20 * 60
FILE_UPLOAD_POLL_MS = 2_000
TEXT_SOURCE_PROCESSING_TIMEOUT_SECONDS = 20 * 60
TEXT_SOURCE_PROCESSING_POLL_MS = 2_000
SOURCE_INDEX_NAME = ".source-urls.json"
SOURCE_REGISTRY = Path("state/source-urls.json")
NOTEBOOK_JOBS_FILE = Path("state/notebook-jobs.json")
DAILY_LIMIT_PATTERNS = (
    re.compile(r"(?:hai\s+)?raggiunto.{0,100}(?:limite|limit).{0,100}(?:giornal|daily|oggi|today)", re.I),
    re.compile(r"(?:limite|limit).{0,100}(?:giornal|daily|oggi|today)", re.I),
    re.compile(r"(?:riprova|try\s+again).{0,100}(?:domani|tomorrow)", re.I),
)


class NotebookLMError(RuntimeError):
    pass


class NotebookLMDailyLimitError(NotebookLMError):
    """NotebookLM has refused the action because its daily allowance is used."""


def _daily_limit_message(page_text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", page_text)
    if any(pattern.search(normalized) for pattern in DAILY_LIMIT_PATTERNS):
        return "You have reached NotebookLM's daily limit. Try again tomorrow."
    return None


def _raise_if_daily_limit(page: Page) -> None:
    try:
        message = _daily_limit_message(page.locator("body").inner_text(timeout=1_000))
    except PlaywrightTimeoutError:
        return
    if message:
        raise NotebookLMDailyLimitError(message)


def _url_key(url: str) -> str:
    """Stable source key for retrying a notebook instead of creating a duplicate."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def _load_notebook_jobs(jobs_file: Path = NOTEBOOK_JOBS_FILE) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(jobs_file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remember_notebook_job(url: str, title: str, state: str, jobs_file: Path = NOTEBOOK_JOBS_FILE) -> None:
    jobs = _load_notebook_jobs(jobs_file)
    jobs[_url_key(url)] = {"title": title, "state": state}
    _write_text_atomically(jobs_file, json.dumps(jobs, ensure_ascii=False, indent=2) + "\n")


def _forget_notebook_job(url: str, jobs_file: Path = NOTEBOOK_JOBS_FILE) -> None:
    jobs = _load_notebook_jobs(jobs_file)
    if jobs.pop(_url_key(url), None) is not None:
        _write_text_atomically(jobs_file, json.dumps(jobs, ensure_ascii=False, indent=2) + "\n")


def _safe_filename(value: str) -> str:
    # Preserve the user-facing title (including spaces) and change only characters
    # that cannot be used in a portable filename.
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", value).strip()[:120] or "audio-overview"


def normalize_title(value: str, today: date | None = None) -> str:
    """Return a notebook title that is also safe to reuse as a filename."""
    text = re.sub(r"^\d{4}\.\d{2}\.\d{2}\s+", "", value.strip())
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-") or "Audio Overview"
    return f"{(today or date.today()).strftime('%Y.%m.%d')} {text}"[:120]


def record_source_url(output_dir: Path, title: str, url: str, text: str | None = None) -> None:
    """Persist source data until the audio download makes the task successful.

    The visible Markdown file is deliberately not created here: failed or pending
    NotebookLM jobs must not leave an apparently completed source file behind.
    """
    payload = {"url": url, "text": text} if text is not None else url
    # Keep operational metadata in state rather than cluttering the user's
    # output folder.  Older output-local indexes are still read as a fallback
    # below, so previous partially completed downloads remain recoverable.
    SOURCE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    try:
        index = json.loads(SOURCE_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {}
    index[title] = payload
    _write_text_atomically(SOURCE_REGISTRY, json.dumps(index, ensure_ascii=False, indent=2) + "\n")


def _source_indexes(output_dir: Path) -> list[Path]:
    """Return current and legacy metadata locations without duplicates."""
    candidates = (SOURCE_REGISTRY, output_dir / SOURCE_INDEX_NAME, Path("AudioOverview") / SOURCE_INDEX_NAME)
    result: list[Path] = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def _source_entry(output_dir: Path, title: str) -> str | dict[str, str] | None:
    for index_file in _source_indexes(output_dir):
        try:
            entry = json.loads(index_file.read_text(encoding="utf-8")).get(title)
            if isinstance(entry, str) or isinstance(entry, dict):
                return entry
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _source_url(output_dir: Path, title: str) -> str | None:
    entry = _source_entry(output_dir, title)
    if isinstance(entry, str):  # Compatibility with source indexes written before this change.
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("url"), str):
        return entry["url"]
    source_file = output_dir / f"{_safe_filename(title)}.md"
    try:
        match = re.search(r"^(?:Source|Fonte):\s*(https?://\S+)", source_file.read_text(encoding="utf-8"), re.MULTILINE)
        return match.group(1) if match else None
    except OSError:
        return None


def _source_text(output_dir: Path, title: str) -> str | None:
    entry = _source_entry(output_dir, title)
    if isinstance(entry, dict) and isinstance(entry.get("text"), str):
        return entry["text"]
    return None


def _write_source_txt(audio_file: Path, url: str) -> Path:
    txt_file = audio_file.with_suffix(".txt")
    txt_file.write_text(f"{url}\n", encoding="utf-8")
    return txt_file


def _write_source_md(audio_file: Path, title: str, url: str, text: str) -> Path:
    md_file = audio_file.with_suffix(".md")
    md_file.write_text(f"# {title}\n\nSource: {url}\n\n{text}\n", encoding="utf-8")
    return md_file


def _write_text_atomically(path: Path, contents: str) -> None:
    """Write a text file without exposing a partially-written sidecar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def _audio_candidates(destination: Path, title: str) -> list[Path]:
    """Return files created for exactly one notebook title, including retries."""
    stem = _safe_filename(title)
    pattern = re.compile(rf"^{re.escape(stem)}(?:-\d+)?\.mp3$")
    return sorted((path for path in destination.glob("*.mp3") if pattern.match(path.name)), key=lambda path: path.stat().st_mtime)


def _is_complete_audio(audio_file: Path, include_markdown: bool = True) -> bool:
    return (
        audio_file.is_file()
        and audio_file.stat().st_size > 0
        and audio_file.with_suffix(".txt").is_file()
        and (not include_markdown or audio_file.with_suffix(".md").is_file())
    )


def _next_audio_path(destination: Path, title: str) -> Path:
    output = destination / f"{_safe_filename(title)}.mp3"
    suffix = 2
    while output.exists():
        output = destination / f"{_safe_filename(title)}-{suffix}.mp3"
        suffix += 1
    return output


def _publish_audio_bundle(
    staged_audio: Path, destination: Path, title: str, url: str, text: str, include_markdown: bool = True,
) -> Path:
    """Publish an MP3 and its source files as one recoverable local transaction.

    A directory cannot atomically rename three individual files.  We therefore stage
    every file first, then publish them with atomic renames; if any publish fails,
    only files created by this attempt are removed.  The notebook is renamed only
    after this function returns successfully.
    """
    output = _next_audio_path(destination, title)
    staged_txt = staged_audio.with_suffix(".txt")
    _write_text_atomically(staged_txt, f"{url}\n")
    staged_files = [(staged_audio, output), (staged_txt, output.with_suffix(".txt"))]
    if include_markdown:
        staged_md = staged_audio.with_suffix(".md")
        _write_text_atomically(staged_md, f"# {title}\n\nSource: {url}\n\n{text}\n")
        staged_files.append((staged_md, output.with_suffix(".md")))
    published: list[Path] = []
    try:
        for source, target in staged_files:
            os.replace(source, target)
            published.append(target)
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return output


def _publish_missing_sidecars(
    audio_file: Path, title: str, url: str, text: str, include_markdown: bool = True,
) -> None:
    """Finish a bundle left by an older version which saved only the MP3."""
    if audio_file.with_suffix(".txt").exists() or (include_markdown and audio_file.with_suffix(".md").exists()):
        raise NotebookLMError(f"Sidecar files are incomplete for: {audio_file.name}")
    staging = audio_file.parent / f".audio-overview-pending-{uuid4().hex}"
    staging.mkdir()
    staged_txt = staging / audio_file.with_suffix(".txt").name
    published: list[Path] = []
    try:
        _write_text_atomically(staged_txt, f"{url}\n")
        staged_files = [(staged_txt, audio_file.with_suffix(".txt"))]
        if include_markdown:
            staged_md = staging / audio_file.with_suffix(".md").name
            _write_text_atomically(staged_md, f"# {title}\n\nSource: {url}\n\n{text}\n")
            staged_files.append((staged_md, audio_file.with_suffix(".md")))
        for source, target in staged_files:
            os.replace(source, target)
            published.append(target)
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _done_title(title: str) -> str:
    return title if title.startswith("[DONE]") else f"[DONE] {title}"


def _click_first(page: Page, labels: list[str], timeout: int = 20_000) -> None:
    """Click the first visible localized button/link matching one of the labels."""
    deadline = monotonic() + timeout / 1_000
    while monotonic() < deadline:
        for label in labels:
            candidates = (
                page.get_by_role("button", name=re.compile(label, re.I)),
                page.locator("button").filter(has_text=re.compile(label, re.I)),
            )
            for locator in candidates:
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    try:
                        if candidate.is_visible() and candidate.is_enabled():
                            # NotebookLM replaces the clicked button during its dialog animation.
                            # Do not wait for that detached element after dispatching the click.
                            candidate.click(timeout=3_000, force=True, no_wait_after=True)
                            return
                    except PlaywrightTimeoutError:
                        continue
        page.wait_for_timeout(250)
    raise NotebookLMError(f"Controllo NotebookLM non trovato: {', '.join(labels)}")


def _debug(page: Page, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=folder / "notebooklm-error.png", full_page=True)
        (folder / "notebooklm-error.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        # Preserve the originating error if the browser was already closed.
        pass


def _wait_for_signin_or_home(page: Page, on_status: Callable[[str], None]) -> None:
    page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
    on_status("Apertura di NotebookLM: accedi a Google se richiesto…")
    create_buttons = page.get_by_role("button", name=re.compile("new notebook|nuovo notebook|crea nuovo", re.I))
    deadline = monotonic() + 180
    while monotonic() < deadline:
        _raise_if_daily_limit(page)
        for index in range(create_buttons.count()):
            candidate = create_buttons.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    # NotebookLM can show its shell before the controls have
                    # finished hydrating.  Confirm the button stays ready.
                    page.wait_for_timeout(750)
                    if candidate.is_visible() and candidate.is_enabled():
                        on_status("NotebookLM is ready.")
                        return
            except PlaywrightTimeoutError:
                continue
        page.wait_for_timeout(250)
    raise NotebookLMError("NotebookLM sign-in did not complete within 3 minutes.")


def _add_copied_text(
    page: Page, title: str, url: str, text: str, on_status: Callable[[str], None],
    on_notebook_created: Callable[[], None] | None = None,
) -> None:
    """Import extracted text instead of asking NotebookLM to fetch the external URL.

    Many publishers block NotebookLM's crawler even when the local extractor can
    access the article.  Supplying the extracted text makes those sources usable.
    """
    on_status("Creating the notebook and importing extracted text…")
    _click_first(page, ["new notebook", "nuovo notebook", "crea nuovo"])
    if on_notebook_created:
        on_notebook_created()
    page.wait_for_timeout(750)
    _raise_if_daily_limit(page)
    _set_notebook_title(page, title)
    on_status(f"Notebook created with title: {title}")
    # NotebookLM replaced the old ``add-source-button`` class in its current
    # interface.  Its accessible label has remained stable across the Italian
    # and English UIs, so keep the class as a legacy fallback only.
    add_source_button = _last_visible(page.locator(
        'button[aria-label*="aggiungi fonte" i], '
        'button[aria-label*="add source" i], '
        'button.add-source-button'
    ))
    # Native DOM click reliably reaches NotebookLM's Angular handler.
    add_source_button.evaluate("button => button.click()")
    page.wait_for_timeout(500)
    copied_text_button = page.locator("button.drop-zone-icon-button").filter(has_text=re.compile("testo copiato|copied text", re.I)).last
    copied_text_button.wait_for(state="visible", timeout=20_000)
    copied_text_button.click(force=True, no_wait_after=True)
    page.wait_for_timeout(500)
    url_input = _last_visible(page.locator("textarea"))
    url_input.fill(f"Source: {url}\n\n{text[:TEXT_LIMIT]}", timeout=20_000)
    # Import labels vary between NotebookLM releases and account languages.
    _click_first(page, ["insert", "inserisci", "add", "aggiungi", "import", "importa"])
    _wait_for_copied_text_ready(page, on_status)


def _wait_for_copied_text_ready(page: Page, on_status: Callable[[str], None]) -> None:
    """Wait for NotebookLM to finish indexing a pasted-text source.

    Clicking ``Inserisci`` only submits the text.  NotebookLM adds the source
    card before its indexing phase has necessarily completed, so a short fixed
    delay can start an Audio Overview from an incomplete source.
    """
    busy = re.compile(r"caricamento|carica in corso|elaborazione|indicizz|uploading|processing|indexing", re.I)
    failed = re.compile(r"errore|non (?:è|e') stato possibile|failed|unsupported|non supportato", re.I)
    deadline = monotonic() + TEXT_SOURCE_PROCESSING_TIMEOUT_SECONDS
    ready_checks = 0
    reported_wait = False
    while monotonic() < deadline:
        _raise_if_daily_limit(page)
        sources = page.locator(".single-source-container")
        source = None
        for index in range(sources.count() - 1, -1, -1):
            candidate = sources.nth(index)
            try:
                if candidate.is_visible():
                    source = candidate
                    break
            except PlaywrightTimeoutError:
                continue
        if source is None:
            ready_checks = 0
        else:
            try:
                source_text = source.inner_text(timeout=1_000)
                source_class = source.get_attribute("class") or ""
                if failed.search(f"{source_text} {source_class}"):
                    raise NotebookLMError("NotebookLM could not analyze the source text.")
                spinner_visible = source.locator(
                    "mat-spinner, mat-progress-spinner, .mat-mdc-progress-spinner, "
                    '[role="progressbar"], [aria-busy="true"], .loading-spinner, '
                    ".loading-indicator, .spinner"
                ).evaluate_all(
                    "elements => elements.some(element => { const style = getComputedStyle(element); return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length; })"
                )
            except PlaywrightTimeoutError:
                ready_checks = 0
                spinner_visible = True
                source_text = ""
                source_class = ""
            if spinner_visible or busy.search(f"{source_text} {source_class}"):
                ready_checks = 0
            else:
                # NotebookLM can render the card one repaint before changing it
                # to its processing state.  Three clean reads avoid that race.
                ready_checks += 1
                if ready_checks >= 3:
                    on_status("Text import and analysis completed.")
                    return
        if not reported_wait:
            on_status("Waiting for NotebookLM to finish importing…")
            reported_wait = True
        page.wait_for_timeout(TEXT_SOURCE_PROCESSING_POLL_MS)
    raise NotebookLMError("Text import or analysis did not complete within 20 minutes.")


def _add_local_file(
    page: Page, title: str, file_path: Path, on_status: Callable[[str], None],
    on_notebook_created: Callable[[], None] | None = None,
) -> None:
    """Create a notebook and import one local file through NotebookLM's uploader."""
    on_status("Creating the notebook and uploading the local file…")
    _click_first(page, ["new notebook", "nuovo notebook", "crea nuovo"])
    if on_notebook_created:
        on_notebook_created()
    page.wait_for_timeout(750)
    _raise_if_daily_limit(page)
    _set_notebook_title(page, title)
    on_status(f"Notebook created with title: {title}")
    add_source_button = _last_visible(page.locator(
        'button[aria-label*="aggiungi fonte" i], '
        'button[aria-label*="add source" i], '
        'button.add-source-button'
    ))
    add_source_button.evaluate("button => button.click()")
    page.wait_for_timeout(500)

    upload_button = page.locator("button.drop-zone-icon-button").filter(
        has_text=re.compile(r"carica.*file|upload.*file|file.*upload", re.I)
    ).last
    try:
        upload_button.wait_for(state="visible", timeout=10_000)
        # Intercept the native chooser at the click that opens it.  Looking for
        # an input afterwards is too late: on macOS the system picker may then
        # be modal and require manual interaction.
        with page.expect_file_chooser(timeout=10_000) as chooser_info:
            upload_button.click(force=True, no_wait_after=True)
        chooser_info.value.set_files(str(file_path), timeout=10_000)
    except PlaywrightTimeoutError as exc:
        raise NotebookLMError("NotebookLM's file-upload option was not found.") from exc
    on_status(f"File uploaded to NotebookLM: {file_path.name}")


def _wait_for_local_file_ready(page: Page, file_path: Path, on_status: Callable[[str], None]) -> None:
    """Wait until the uploaded source is visible and no longer being processed.

    Selecting a file only confirms that the browser handed it to NotebookLM; the
    service can still spend several minutes uploading and indexing it.  Starting
    an Audio Overview before this finishes produces incomplete or failed audio.
    """
    source_name = re.compile(re.escape(file_path.name), re.I)
    busy = re.compile(r"caricamento|carica in corso|elaborazione|indicizz|uploading|processing|indexing", re.I)
    failed = re.compile(r"errore|non (?:è|e') stato possibile|failed|unsupported|non supportato", re.I)
    deadline = monotonic() + FILE_UPLOAD_TIMEOUT_SECONDS
    ready_checks = 0
    reported_wait = False
    while monotonic() < deadline:
        _raise_if_daily_limit(page)
        visible_source = None
        labels = page.get_by_text(source_name)
        for index in range(labels.count() - 1, -1, -1):
            candidate = labels.nth(index)
            try:
                if candidate.is_visible():
                    visible_source = candidate
                    break
            except PlaywrightTimeoutError:
                continue
        if visible_source is None:
            ready_checks = 0
        else:
            # NotebookLM puts the progress label next to the filename.  Inspect
            # its nearby containers rather than the whole page, which may show
            # unrelated background activity.
            nearby_text = ""
            container = visible_source
            for _ in range(4):
                try:
                    nearby_text += " " + container.inner_text(timeout=1_000)
                    container = container.locator("xpath=..")
                except PlaywrightTimeoutError:
                    break
            if failed.search(nearby_text):
                raise NotebookLMError(f"NotebookLM could not analyze the file: {file_path.name}")
            spinner_visible = visible_source.evaluate(
                """element => {
                    const spinnerSelector = [
                      'mat-spinner', 'mat-progress-spinner', '.mat-mdc-progress-spinner',
                      '[role="progressbar"]', '[aria-busy="true"]', '.loading-spinner',
                      '.loading-indicator', '.spinner'
                    ].join(', ');
                    let node = element;
                    for (let level = 0; node && level < 5; level += 1, node = node.parentElement) {
                      // Do not climb as far as the whole page: unrelated work in
                      // another notebook panel must not block this source.
                      if ((node.innerText || '').length > 1500) break;
                      for (const spinner of node.querySelectorAll(spinnerSelector)) {
                        const style = window.getComputedStyle(spinner);
                        if (style.display !== 'none' && style.visibility !== 'hidden'
                            && spinner.getClientRects().length) return true;
                      }
                    }
                    return false;
                }"""
            )
            if not spinner_visible:
                # Some NotebookLM releases render the source spinner in a
                # sibling overlay instead of inside the source card.  It is
                # still a visible progress indicator on this notebook page, so
                # treat it as pending rather than risk starting the audio early.
                page_spinners = page.locator(
                    'mat-spinner, mat-progress-spinner, .mat-mdc-progress-spinner, '
                    '[role="progressbar"], [aria-busy="true"], .loading-spinner, '
                    '.loading-indicator, .spinner, mat-icon'
                )
                for index in range(page_spinners.count()):
                    candidate = page_spinners.nth(index)
                    try:
                        if not candidate.is_visible():
                            continue
                        label = " ".join((candidate.inner_text(timeout=500), candidate.get_attribute("class") or ""))
                        if candidate.evaluate("element => element.matches('mat-icon')"):
                            if not re.search(r"progress|loading|autorenew|hourglass", label, re.I):
                                continue
                        spinner_visible = True
                        break
                    except PlaywrightTimeoutError:
                        continue
            if spinner_visible or busy.search(nearby_text):
                ready_checks = 0
            else:
                # Require three consecutive checks: the filename can appear while
                # the upload UI is still transitioning to its progress state.
                ready_checks += 1
                if ready_checks >= 3:
                    on_status(f"Upload and analysis completed: {file_path.name}")
                    return
        if not reported_wait:
            on_status(f"Waiting for NotebookLM to finish uploading: {file_path.name}…")
            reported_wait = True
        page.wait_for_timeout(FILE_UPLOAD_POLL_MS)
    raise NotebookLMError(f"File upload or analysis did not complete within 20 minutes: {file_path.name}")


def _set_notebook_title(page: Page, title: str) -> None:
    """Set the notebook name immediately after creation, before source dialogs exist."""
    title_input = page.locator("editable-project-title input.title-input")
    try:
        title_input.wait_for(state="attached", timeout=10_000)
    except PlaywrightTimeoutError:
        print("Notebook title field not found: NotebookLM's default title will be used.")
        return
    title_widget = page.locator("editable-project-title")
    if title_widget.count():
        try:
            title_widget.click(timeout=1_000)
        except PlaywrightTimeoutError:
            pass
    try:
        title_input.fill(title, timeout=2_000)
        title_input.press("Enter")
        return
    except PlaywrightTimeoutError:
        pass
    selectors = (
        'input[aria-label*="title" i]',
        'input[aria-label*="titolo" i]',
        'input[placeholder*="title" i]',
        'input[placeholder*="titolo" i]',
        '[contenteditable="true"]',
    )
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        try:
            locator.first.fill(title, timeout=1_000)
            locator.first.press("Enter")
            return
        except PlaywrightTimeoutError:
            continue
    print("Notebook title field not found: NotebookLM's default title will be used.")


def _mark_notebook_done(page: Page, title: str, on_status: Callable[[str], None]) -> None:
    """Prefix the notebook title only after both local output files are present."""
    if title.startswith("[DONE]"):
        return
    title_input = page.locator("editable-project-title input.title-input")
    title_input.wait_for(state="attached", timeout=10_000)
    title_widget = page.locator("editable-project-title")
    if title_widget.count():
        title_widget.click(timeout=2_000)
    title_input.fill(_done_title(title), timeout=2_000)
    title_input.press("Enter")
    on_status(f"Notebook marked as complete: {_done_title(title)}")


def _last_visible(locator):
    """Return the last visible instance; NotebookLM keeps hidden controls in its DOM."""
    for index in range(locator.count() - 1, -1, -1):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    raise NotebookLMError("Source text field not found.")


def _start_audio_generation(page: Page, language: str, prompt: str | None, on_status: Callable[[str], None]) -> None:
    # NotebookLM disables the Audio Overview tile once the daily allowance is
    # exhausted.  In that state there is no clickable control, so checking only
    # after a click turns a clear service limit into the misleading "button not
    # available" error.  Inspect the page before looking for an enabled tile.
    _raise_if_daily_limit(page)
    audio_buttons = page.locator(
        '[role="button"].create-artifact-button-container[aria-label*="overview audio" i], '
        '[role="button"].create-artifact-button-container[aria-label*="audio overview" i]'
    )
    audio_button = None
    deadline = monotonic() + 20
    while monotonic() < deadline:
        for index in range(audio_buttons.count()):
            candidate = audio_buttons.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    audio_button = candidate
                    break
            except PlaywrightTimeoutError:
                continue
        if audio_button is not None:
            break
        page.wait_for_timeout(250)
    if audio_button is None:
        raise NotebookLMError("Audio Overview creation button is unavailable.")
    audio_button.evaluate("button => button.click()")
    page.wait_for_timeout(750)
    generating = page.get_by_role(
        "button", name=re.compile("generazione.*overview audio|audio overview.*generating", re.I)
    ).first
    # The creation tile can either start immediately or open an Audio Overview
    # dialog.  Never use a page-wide "Personalizza"/"Genera" match: those labels
    # also occur on mind-map cards and can launch the wrong artifact.
    try:
        if generating.is_visible():
            on_status("Audio Overview started and confirmed by NotebookLM.")
            return
    except PlaywrightTimeoutError:
        pass
    audio_dialog = page.locator('[role="dialog"]').filter(has_text=re.compile("overview audio|audio overview", re.I)).last
    try:
        audio_dialog.wait_for(state="visible", timeout=5_000)
        generate = audio_dialog.get_by_role("button", name=re.compile("^genera$|^generate$", re.I)).last
        generate.wait_for(state="visible", timeout=5_000)
        generate.click()
    except PlaywrightTimeoutError as exc:
        raise NotebookLMError("NotebookLM did not open the Audio Overview panel.") from exc
    deadline = monotonic() + 30
    while monotonic() < deadline:
        _raise_if_daily_limit(page)
        try:
            if generating.is_visible():
                on_status("Audio Overview started and confirmed by NotebookLM.")
                return
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(250)
    _raise_if_daily_limit(page)
    raise NotebookLMError("NotebookLM did not confirm that audio generation started.")


def _save_download(page: Page, title: str, destination: Path, on_status: Callable[[str], None]) -> Path:
    """Download to a private staging directory; caller publishes it only when complete."""
    download_button = page.get_by_role("button", name=re.compile("download|scarica", re.I))
    download_target = None
    for index in range(download_button.count() - 1, -1, -1):
        candidate = download_button.nth(index)
        if candidate.is_visible():
            download_target = candidate
            break
    if download_target is None:
        more_button = page.locator("button.artifact-more-button").last
        more_button.wait_for(state="visible", timeout=5_000)
        more_button.click()
        menu_item = page.get_by_role("menuitem", name=re.compile("download|scarica", re.I)).last
        menu_item.wait_for(state="visible", timeout=5_000)
        download_target = menu_item
    with page.expect_download(timeout=30_000) as download_info:
        on_status(f"Downloading audio: {title}")
        download_target.click()
    download = download_info.value
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / f".audio-overview-pending-{uuid4().hex}"
    staging.mkdir()
    output = staging / f"{_safe_filename(title)}.mp3"
    download.save_as(output)
    if not output.is_file() or output.stat().st_size == 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise NotebookLMError(f"Download MP3 non valido per: {title}")
    return output


def _delete_notebook_by_title(page: Page, title: str, on_status: Callable[[str], None]) -> bool:
    """Delete one exact notebook as compensation for a failed creation flow."""
    page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(1_000)
    list_view = page.get_by_role("button", name=re.compile("visualizzazione elenco|list view", re.I))
    for index in range(list_view.count()):
        candidate = list_view.nth(index)
        try:
            if candidate.is_visible() and candidate.is_enabled():
                candidate.click(timeout=2_000)
                page.wait_for_timeout(300)
                break
        except PlaywrightTimeoutError:
            continue
    cells = page.locator('tr[role="row"] .project-table-title[title]')
    # A timeout just after the title update can leave a successfully renamed
    # notebook behind.  Compensation must still find and delete that same item.
    candidate_titles = (title, _done_title(title))
    title_cell = next(
        (
            cells.nth(index)
            for index in range(cells.count())
            if (cells.nth(index).get_attribute("title") or "") in candidate_titles
        ),
        None,
    )
    if title_cell is None:
        return False
    row = title_cell.locator("xpath=ancestor::tr")
    row.locator('button[aria-label*="Azioni" i], button[aria-label*="actions" i]').first.click(timeout=5_000)
    delete_item = page.get_by_role("menuitem", name=re.compile("elimina|delete", re.I)).last
    delete_item.wait_for(state="visible", timeout=5_000)
    delete_item.click()
    confirm = page.get_by_role("button", name=re.compile("^elimina$|^delete$", re.I)).last
    confirm.wait_for(state="visible", timeout=5_000)
    confirm.click()
    try:
        title_cell.wait_for(state="detached", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    # NotebookLM updates the virtualized home list asynchronously.  Give the
    # deletion time to settle, then reload before any subsequent list lookup.
    page.wait_for_timeout(2_000)
    page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
    on_status(f"Notebook removed: {title}")
    return True


def start_audio_generation(
    title: str, url: str, text: str, output_dir: Path, profile_dir: Path, language: str, prompt: str | None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """Create a notebook, import extracted text, and start generation without waiting."""
    title = normalize_title(title)
    profile_dir.mkdir(parents=True, exist_ok=True)
    record_source_url(output_dir, title, url, text)
    report = on_status or print
    with sync_playwright() as playwright:
        context: BrowserContext = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=False, accept_downloads=True, downloads_path=output_dir
        )
        page = context.pages[0] if context.pages else context.new_page()
        notebook_created = False
        created_in_this_attempt = False

        def mark_notebook_created() -> None:
            nonlocal notebook_created, created_in_this_attempt
            notebook_created = True
            created_in_this_attempt = True
            _remember_notebook_job(url, title, "created")

        try:
            _wait_for_signin_or_home(page, report)
            jobs = _load_notebook_jobs()
            saved = jobs.get(_url_key(url), {})
            retry_title = saved.get("title") if isinstance(saved, dict) else None
            target_title = retry_title or title
            existing = next(((name, href) for name, href in _home_notebooks(page) if name == target_title), None)
            if existing is not None:
                report(f"Existing notebook found; resuming: {target_title}")
                _open_notebook(page, existing[0], existing[1])
                page.wait_for_timeout(750)
                if _has_audio_overview(page):
                    _remember_notebook_job(url, target_title, "audio_started")
                    report("Audio Overview already exists: no new notebook created.")
                    return
                _start_audio_generation(page, language, prompt, report)
                _remember_notebook_job(url, target_title, "audio_started")
                return
            _add_copied_text(page, title, url, text, report, on_notebook_created=mark_notebook_created)
            _start_audio_generation(page, language, prompt, report)
            _remember_notebook_job(url, title, "audio_started")
        except NotebookLMDailyLimitError as exc:
            message = str(exc)
            if notebook_created and created_in_this_attempt:
                try:
                    if not _delete_notebook_by_title(page, title, report):
                        message += " The newly created notebook could not be found for automatic removal."
                    else:
                        _forget_notebook_job(url)
                except Exception as cleanup_exc:
                    message += f" Unable to remove the newly created notebook: {cleanup_exc}"
            debug_dir = output_dir / ".debug"
            _debug(page, debug_dir)
            (debug_dir / "notebooklm-error.txt").write_text(
                f"NotebookLMDailyLimitError: {message}\n", encoding="utf-8"
            )
            raise NotebookLMDailyLimitError(message) from exc
        except Exception as exc:
            debug_dir = output_dir / ".debug"
            _debug(page, debug_dir)
            (debug_dir / "notebooklm-error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            raise
        finally:
            context.close()


def _wait_for_audio_ready(page: Page, title: str, on_status: Callable[[str], None]) -> None:
    """Poll one notebook until its Audio Overview is ready to download."""
    deadline = monotonic() + GENERATION_TIMEOUT_MS / 1_000
    while monotonic() < deadline:
        _raise_if_daily_limit(page)
        audio_items = _artifact_items_with_icon(page, "audio_magic_eraser")
        if audio_items:
            details = " ".join(audio_items[-1].inner_text().split()).lower()
            if not re.search(r"generazione.*in corso|generating|torna tra|come back", details, re.I):
                on_status(f"Audio Overview ready to download: {title}")
                return
            on_status(f"Audio Overview generating: {title}. Checking again in 30 seconds…")
        elif _artifact_items_with_icon(page, "flowchart"):
            raise NotebookLMError(f"NotebookLM generated a mind map instead of an Audio Overview: {title}")
        else:
            on_status(f"Waiting for Audio Overview creation: {title}. Checking again in 30 seconds…")
        page.wait_for_timeout(GENERATION_POLL_MS)
    raise NotebookLMError(f"Audio Overview was not ready within 200 minutes: {title}")


def _save_ready_audio(page: Page, title: str, destination: Path, on_status: Callable[[str], None]) -> Path:
    """Download only the Audio Overview card, never another studio artifact."""
    audio_items = _artifact_items_with_icon(page, "audio_magic_eraser")
    if not audio_items:
        raise NotebookLMError(f"Audio Overview was not found for download: {title}")
    item = audio_items[-1]
    download_target = None
    buttons = item.get_by_role("button", name=re.compile("download|scarica", re.I))
    for index in range(buttons.count() - 1, -1, -1):
        candidate = buttons.nth(index)
        if candidate.is_visible():
            download_target = candidate
            break
    if download_target is None:
        more = item.locator("button.artifact-more-button").last
        more.wait_for(state="visible", timeout=5_000)
        more.click()
        menu_item = page.get_by_role("menuitem", name=re.compile("download|scarica", re.I)).last
        menu_item.wait_for(state="visible", timeout=5_000)
        download_target = menu_item
    with page.expect_download(timeout=30_000) as download_info:
        on_status(f"Downloading Audio Overview: {title}")
        download_target.click()
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / f".audio-overview-pending-{uuid4().hex}"
    staging.mkdir()
    output = staging / f"{_safe_filename(title)}.mp3"
    download_info.value.save_as(output)
    if not output.is_file() or output.stat().st_size == 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise NotebookLMError(f"Download MP3 non valido per: {title}")
    return output


def process_url_to_completion(
    title: str, url: str, text: str, output_dir: Path, profile_dir: Path,
    language: str = "Italian", prompt: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Create, wait for, download, and finalize one URL as an isolated transaction.

    A normal failure removes the notebook created for that attempt and retries once
    from scratch.  A daily-limit error removes the just-created notebook but is
    propagated immediately so callers can stop their queue.
    """
    title = normalize_title(title)
    profile_dir.mkdir(parents=True, exist_ok=True)
    record_source_url(output_dir, title, url, text)
    report = on_status or print
    last_error: Exception | None = None
    for attempt in range(1, MAX_CLEAN_RECREATION_ATTEMPTS + 1):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir, headless=False, accept_downloads=True, downloads_path=output_dir
            )
            page = context.pages[0] if context.pages else context.new_page()
            created = False
            published_audio: Path | None = None

            def mark_created() -> None:
                nonlocal created
                created = True
                _remember_notebook_job(url, title, "created")

            try:
                report(f"Processing {title} to completion ({attempt}/{MAX_CLEAN_RECREATION_ATTEMPTS})…")
                _wait_for_signin_or_home(page, report)
                _add_copied_text(page, title, url, text, report, on_notebook_created=mark_created)
                _start_audio_generation(page, language, prompt, report)
                _wait_for_audio_ready(page, title, report)
                staged_audio = _save_ready_audio(page, title, output_dir, report)
                try:
                    published_audio = _publish_audio_bundle(staged_audio, output_dir, title, url, text)
                finally:
                    shutil.rmtree(staged_audio.parent, ignore_errors=True)
                _mark_notebook_done(page, title, report)
                _remember_notebook_job(url, title, "completed")
                return published_audio
            except NotebookLMDailyLimitError:
                if created:
                    try:
                        if _delete_notebook_by_title(page, title, report):
                            _forget_notebook_job(url)
                    except Exception:
                        pass
                raise
            except Exception as exc:
                last_error = exc
                if published_audio is not None:
                    # The local bundle is not a completed result until the
                    # corresponding notebook has been marked [DONE].  Remove
                    # only this attempt's files before recreating from zero.
                    for path in (published_audio, published_audio.with_suffix(".txt"), published_audio.with_suffix(".md")):
                        path.unlink(missing_ok=True)
                if created:
                    try:
                        if not _delete_notebook_by_title(page, title, report):
                            raise NotebookLMError(f"Notebook to recreate was not found: {title}") from exc
                        _forget_notebook_job(url)
                    except Exception as cleanup_exc:
                        raise NotebookLMError(f"Error for {title}; notebook cleanup failed: {cleanup_exc}") from exc
                if attempt < MAX_CLEAN_RECREATION_ATTEMPTS:
                    report(f"Error for {title}; notebook removed. Clean recreation ({attempt + 1}/{MAX_CLEAN_RECREATION_ATTEMPTS})…")
            finally:
                context.close()
    raise NotebookLMError(f"Processing did not complete after {MAX_CLEAN_RECREATION_ATTEMPTS} attempts: {last_error}")


def process_file_to_completion(
    file_path: Path, output_dir: Path, profile_dir: Path,
    language: str = "Italian", prompt: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Upload a queued local file, then create, download and finalize its audio."""
    file_path = file_path.expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Local file not found: {file_path}")
    # Keep the full filesystem path in the user-facing TXT/MD sidecars.  The
    # file URI remains an internal, stable key for the notebook retry registry.
    source_ref = str(file_path)
    job_key = file_path.as_uri()
    source_text = f"Local file uploaded to NotebookLM: {file_path.name}\nPath: {file_path}"
    title = normalize_title(file_path.stem)
    profile_dir.mkdir(parents=True, exist_ok=True)
    record_source_url(output_dir, title, source_ref, source_text)
    report = on_status or print
    last_error: Exception | None = None
    for attempt in range(1, MAX_CLEAN_RECREATION_ATTEMPTS + 1):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir, headless=False, accept_downloads=True, downloads_path=output_dir
            )
            page = context.pages[0] if context.pages else context.new_page()
            created = False
            published_audio: Path | None = None

            def mark_created() -> None:
                nonlocal created
                created = True
                _remember_notebook_job(job_key, title, "created")

            try:
                report(f"Processing {file_path.name} to completion ({attempt}/{MAX_CLEAN_RECREATION_ATTEMPTS})…")
                _wait_for_signin_or_home(page, report)
                jobs = _load_notebook_jobs()
                saved = jobs.get(_url_key(job_key), {})
                retry_title = saved.get("title") if isinstance(saved, dict) else None
                target_title = retry_title or title
                existing = next(((name, href) for name, href in _home_notebooks(page) if name == target_title), None)
                if existing is not None:
                    report(f"Existing file notebook found; resuming: {target_title}")
                    _open_notebook(page, existing[0], existing[1])
                    page.wait_for_timeout(750)
                if existing is None:
                    _add_local_file(page, title, file_path, report, on_notebook_created=mark_created)
                if _has_audio_overview(page):
                    report("Audio Overview already exists: waiting for completion.")
                else:
                    _wait_for_local_file_ready(page, file_path, report)
                    _start_audio_generation(page, language, prompt, report)
                _wait_for_audio_ready(page, title, report)
                staged_audio = _save_ready_audio(page, title, output_dir, report)
                try:
                    published_audio = _publish_audio_bundle(
                        staged_audio, output_dir, title, source_ref, source_text, include_markdown=False
                    )
                finally:
                    shutil.rmtree(staged_audio.parent, ignore_errors=True)
                _mark_notebook_done(page, title, report)
                _remember_notebook_job(job_key, title, "completed")
                return published_audio
            except NotebookLMDailyLimitError:
                if created:
                    try:
                        if _delete_notebook_by_title(page, title, report):
                            _forget_notebook_job(job_key)
                    except Exception:
                        pass
                raise
            except Exception as exc:
                last_error = exc
                if published_audio is not None:
                    for path in (published_audio, published_audio.with_suffix(".txt"), published_audio.with_suffix(".md")):
                        path.unlink(missing_ok=True)
                if created:
                    try:
                        if not _delete_notebook_by_title(page, title, report):
                            raise NotebookLMError(f"Notebook to recreate was not found: {title}") from exc
                        _forget_notebook_job(job_key)
                    except Exception as cleanup_exc:
                        raise NotebookLMError(f"Error for {file_path.name}; notebook cleanup failed: {cleanup_exc}") from exc
                if attempt < MAX_CLEAN_RECREATION_ATTEMPTS:
                    report(f"Error for {file_path.name}; notebook removed. Clean recreation ({attempt + 1}/{MAX_CLEAN_RECREATION_ATTEMPTS})…")
            finally:
                context.close()
    raise NotebookLMError(f"File processing did not complete after {MAX_CLEAN_RECREATION_ATTEMPTS} attempts: {last_error}")


def download_ready_audios(
    output_dir: Path, profile_dir: Path, on_status: Callable[[str], None] | None = None,
) -> tuple[list[Path], int]:
    """Visit all notebooks reachable from the home page and download ready Audio Overviews.

    Returns downloaded files and the number of notebooks skipped because no ready audio
    download control was available.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    report = on_status or print
    downloaded: list[Path] = []
    skipped = 0
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=False, accept_downloads=True, downloads_path=output_dir
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _wait_for_signin_or_home(page, report)
            notebooks: list[tuple[str, str | None]] = []
            grid_links = page.locator('a.primary-action-button[href*="/notebook/"]')
            for index in range(grid_links.count()):
                link = grid_links.nth(index)
                href = link.get_attribute("href")
                labels = (link.get_attribute("aria-labelledby") or "").split()
                title_id = next((label for label in labels if label.endswith("-title")), None)
                title = page.locator(f"#{title_id}").inner_text().strip() if title_id else "Audio Overview"
                if href and (title, href) not in notebooks:
                    notebooks.append((title, href))
            title_cells = page.locator('tr[role="row"] .project-table-title[title]')
            for index in range(title_cells.count()):
                title = title_cells.nth(index).get_attribute("title")
                if title and not any(existing == title for existing, _ in notebooks):
                    notebooks.append((title, None))
            if not notebooks:
                raise NotebookLMError("No notebooks are available on the home page.")
            for title, href in notebooks:
                if title.startswith("[DONE]"):
                    report(f"Notebook is already complete; skipping: {title}")
                    continue
                try:
                    if href:
                        page.goto(urljoin("https://notebooklm.google.com", href), wait_until="domcontentloaded")
                    else:
                        row = page.locator('tr[role="row"]').filter(has_text=title).last
                        row.wait_for(state="visible", timeout=10_000)
                        row.click(timeout=10_000)
                        page.wait_for_url(re.compile(r".*/notebook/[^/?]+"), timeout=15_000)
                    page.wait_for_timeout(750)

                    # If an earlier run downloaded an MP3 but stopped before the
                    # sidecars/rename, repair it without downloading a duplicate.
                    source_url = _source_url(output_dir, title)
                    source_text = _source_text(output_dir, title)
                    is_local_file = bool(source_url and Path(source_url).is_absolute())
                    candidates = _audio_candidates(output_dir, title)
                    complete_audio = next(
                        (item for item in reversed(candidates) if _is_complete_audio(item, include_markdown=not is_local_file)),
                        None,
                    )
                    if complete_audio is not None:
                        _mark_notebook_done(page, title, report)
                        downloaded.append(complete_audio)
                        report(f"Files already complete; notebook updated: {title}")
                        continue

                    if not source_url or source_text is None:
                        raise NotebookLMError(f"Dati sorgente non disponibili per: {title}")

                    # Recover a bare MP3 created by a previous version before
                    # attempting a new browser download.
                    if candidates:
                        audio_file = candidates[-1]
                        _publish_missing_sidecars(
                            audio_file, title, source_url, source_text, include_markdown=not is_local_file
                        )
                        _mark_notebook_done(page, title, report)
                        downloaded.append(audio_file)
                        report(f"Previous MP3 completed: {title}")
                        continue

                    staged_audio = _save_download(page, title, output_dir, report)
                    try:
                        audio_file = _publish_audio_bundle(
                            staged_audio, output_dir, title, source_url, source_text, include_markdown=not is_local_file
                        )
                    finally:
                        shutil.rmtree(staged_audio.parent, ignore_errors=True)
                    _mark_notebook_done(page, title, report)
                    downloaded.append(audio_file)
                except Exception as exc:
                    _debug(page, output_dir / ".debug" / "download-failed" / _safe_filename(title))
                    skipped += 1
                    report(f"Notebook not completed; skipping: {title} ({exc})")
                finally:
                    # A failure for one notebook must never prevent the next one.
                    try:
                        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
                        page.wait_for_timeout(500)
                    except Exception as exc:
                        report(f"Ritorno alla pagina iniziale non riuscito: {exc}")
            return downloaded, skipped
        except Exception as exc:
            _debug(page, output_dir / ".debug")
            (output_dir / ".debug" / "notebooklm-error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            raise
        finally:
            context.close()


def _home_notebooks(page: Page) -> list[tuple[str, str | None]]:
    """Collect all notebooks, including virtualized rows loaded while scrolling."""
    notebooks: list[tuple[str, str | None]] = []
    unchanged_scrolls = 0
    for _ in range(50):
        before = len(notebooks)
        grid_links = page.locator('a.primary-action-button[href*="/notebook/"]')
        for index in range(grid_links.count()):
            link = grid_links.nth(index)
            href = link.get_attribute("href")
            labels = (link.get_attribute("aria-labelledby") or "").split()
            title_id = next((label for label in labels if label.endswith("-title")), None)
            title = page.locator(f"#{title_id}").inner_text().strip() if title_id else "Audio Overview"
            if href and (title, href) not in notebooks:
                notebooks.append((title, href))
        title_cells = page.locator('tr[role="row"] .project-table-title[title]')
        for index in range(title_cells.count()):
            title = title_cells.nth(index).get_attribute("title")
            if title and not any(existing == title for existing, _ in notebooks):
                notebooks.append((title, None))
        unchanged_scrolls = unchanged_scrolls + 1 if len(notebooks) == before else 0
        if unchanged_scrolls >= 3:
            break
        page.mouse.wheel(0, 1_200)
        page.wait_for_timeout(400)
    page.mouse.wheel(0, -100_000)
    page.wait_for_timeout(300)
    return notebooks


def _open_notebook(page: Page, title: str, href: str | None) -> None:
    """Open a notebook even when its row is currently outside the virtualized list."""
    if href:
        page.goto(urljoin("https://notebooklm.google.com", href), wait_until="domcontentloaded")
        return
    for _ in range(50):
        cells = page.locator('tr[role="row"] .project-table-title[title]')
        for index in range(cells.count()):
            cell = cells.nth(index)
            if (cell.get_attribute("title") or "") == title:
                row = cell.locator("xpath=ancestor::tr")
                row.click(timeout=10_000)
                page.wait_for_url(re.compile(r".*/notebook/[^/?]+"), timeout=15_000)
                return
        page.mouse.wheel(0, 1_200)
        page.wait_for_timeout(400)
    raise NotebookLMError(f"Notebook not found in the list: {title}")


def _has_audio_overview(page: Page) -> bool:
    """Return whether the studio library contains a running or ready audio overview."""
    audio_items = page.locator(
        'artifact-library-item button[aria-description*="overview audio" i], '
        'artifact-library-item button[aria-description*="audio overview" i], '
        'artifact-library-item:has(mat-icon:text-is("audio_magic_eraser"))'
    )
    for index in range(audio_items.count()):
        try:
            if audio_items.nth(index).is_visible():
                return True
        except PlaywrightTimeoutError:
            continue
    return bool(_artifact_items_with_icon(page, "audio_magic_eraser"))


def _artifact_items_with_icon(page: Page, icon_name: str) -> list:
    """Find library cards by their stable Material icon, not their translated label."""
    matches = []
    items = page.locator("artifact-library-item")
    for index in range(items.count()):
        item = items.nth(index)
        try:
            if not item.is_visible():
                continue
            icons = item.locator("mat-icon").all_inner_texts()
            if any(icon.strip() == icon_name for icon in icons):
                matches.append(item)
        except PlaywrightTimeoutError:
            continue
    return matches


def _delete_mind_maps(page: Page, title: str, on_status: Callable[[str], None]) -> int:
    """Delete every generated mind map in one notebook before starting audio."""
    removed = 0
    while True:
        maps = _artifact_items_with_icon(page, "flowchart")
        if not maps:
            return removed
        target = maps[0]
        maps_before = len(maps)
        more = target.locator("button.artifact-more-button").last
        more.wait_for(state="visible", timeout=5_000)
        more.click()
        delete_item = page.get_by_role("menuitem", name=re.compile("elimina|delete|rimuovi|remove", re.I)).last
        delete_item.wait_for(state="visible", timeout=5_000)
        delete_item.click()
        confirm = page.get_by_role("button", name=re.compile("elimina|delete|rimuovi|remove", re.I)).last
        confirm.wait_for(state="visible", timeout=5_000)
        confirm.click()
        try:
            target.wait_for(state="detached", timeout=15_000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(1_500)
        if len(_artifact_items_with_icon(page, "flowchart")) >= maps_before:
            raise NotebookLMError(f"NotebookLM did not confirm mind-map deletion: {title}")
        removed += 1
        on_status(f"Mind map deleted: {title}")


def verify_audio_overviews(
    profile_dir: Path, on_status: Callable[[str], None] | None = None,
) -> tuple[int, int, int, int, bool]:
    """Ensure every pending notebook has an Audio Overview instead of mind maps.

    Returns ``(already_audio, maps_deleted, audio_started, failed, limit_reached)``.
    Individual notebook errors are reported and do not affect the next notebook;
    a daily NotebookLM limit stops the operation because subsequent starts would
    necessarily fail as well.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    report = on_status or print
    already_audio = maps_deleted = audio_started = failed = 0
    limit_reached = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(user_data_dir=profile_dir, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _wait_for_signin_or_home(page, report)
            notebooks = _home_notebooks(page)
            if not notebooks:
                raise NotebookLMError("No notebooks are available on the home page.")
            for title, href in notebooks:
                if title.startswith("[DONE]"):
                    continue
                try:
                    _open_notebook(page, title, href)
                    page.wait_for_timeout(750)
                    if _has_audio_overview(page):
                        already_audio += 1
                        report(f"Audio Overview already exists: {title}")
                        continue
                    maps_deleted += _delete_mind_maps(page, title, report)
                    report(f"Starting missing Audio Overview: {title}")
                    _start_audio_generation(page, "Italian", None, report)
                    audio_started += 1
                except NotebookLMDailyLimitError as exc:
                    limit_reached = True
                    report(f"{exc} Checking stopped.")
                    break
                except Exception as exc:
                    failed += 1
                    _debug(page, Path("AudioOverview") / ".debug" / "verify-failed" / _safe_filename(title))
                    report(f"Notebook was not checked; skipping: {title} ({exc})")
                finally:
                    try:
                        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
                        page.wait_for_timeout(500)
                    except Exception as exc:
                        report(f"Ritorno alla pagina iniziale non riuscito: {exc}")
            return already_audio, maps_deleted, audio_started, failed, limit_reached
        except Exception as exc:
            _debug(page, Path("AudioOverview") / ".debug" / "verify-failed")
            raise NotebookLMError(f"NotebookLM check did not complete: {exc}") from exc
        finally:
            context.close()


def delete_done_notebooks(
    profile_dir: Path, on_status: Callable[[str], None] | None = None,
) -> int:
    """Permanently remove notebooks whose title starts with ``[DONE]``.

    Callers must obtain explicit user confirmation immediately before this function.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    report = on_status or print
    deleted = 0
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(user_data_dir=profile_dir, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _wait_for_signin_or_home(page, report)
            failed_titles: set[str] = set()
            while True:
                # NotebookLM exposes project actions reliably in the list view;
                # in the grid view they may be hidden until a card is hovered.
                list_view = page.get_by_role("button", name=re.compile("visualizzazione elenco|list view", re.I))
                for index in range(list_view.count()):
                    candidate = list_view.nth(index)
                    try:
                        if candidate.is_visible() and candidate.is_enabled():
                            candidate.click(timeout=2_000)
                            page.wait_for_timeout(300)
                            break
                    except PlaywrightTimeoutError:
                        continue
                title_cell = None
                cells = page.locator('tr[role="row"] .project-table-title[title]')
                for index in range(cells.count()):
                    candidate = cells.nth(index)
                    candidate_title = candidate.get_attribute("title") or ""
                    if candidate_title.startswith("[DONE]") and candidate_title not in failed_titles:
                        title_cell = candidate
                        break
                if title_cell is None:
                    break
                title = title_cell.get_attribute("title") or "[DONE]"
                try:
                    row = title_cell.locator("xpath=ancestor::tr")
                    actions = row.locator('button[aria-label*="Azioni" i], button[aria-label*="actions" i]')
                    actions.first.click(timeout=5_000)
                    delete_item = page.get_by_role("menuitem", name=re.compile("elimina|delete", re.I)).last
                    delete_item.wait_for(state="visible", timeout=5_000)
                    delete_item.click()
                    # NotebookLM asks for a second confirmation in its own dialog.
                    confirm = page.get_by_role("button", name=re.compile("^elimina$|^delete$", re.I)).last
                    confirm.wait_for(state="visible", timeout=5_000)
                    confirm.click()
                    # The delete request is asynchronous.  Do not inspect a
                    # stale list: wait for this exact row to disappear, then
                    # reload the home page before looking for the next [DONE].
                    try:
                        title_cell.wait_for(state="detached", timeout=15_000)
                    except PlaywrightTimeoutError:
                        # Some NotebookLM releases retain a virtualized row in
                        # the DOM; a full home reload is the authoritative check.
                        pass
                    page.wait_for_timeout(2_000)
                    page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
                    report(f"Notebook deleted: {title}")
                    deleted += 1
                except Exception as exc:
                    # A broken menu/dialog for one notebook must not leave the
                    # remaining completed notebooks undeleted.
                    failed_titles.add(title)
                    _debug(page, Path("AudioOverview") / ".debug" / "delete-failed" / _safe_filename(title))
                    report(f"Notebook was not deleted; skipping: {title} ({exc})")
            if failed_titles:
                report(f"[DONE] notebooks not deleted: {len(failed_titles)}")
            return deleted
        except Exception as exc:
            _debug(page, Path("AudioOverview") / ".debug")
            raise NotebookLMError(f"[DONE] notebook deletion did not complete: {exc}") from exc
        finally:
            context.close()
