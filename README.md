# Podcast All

Local terminal app for creating and downloading NotebookLM Audio Overviews from
public URLs and local files. URLs and files share a persistent queue; each item
is removed only after a non-empty MP3 and all required companion files are saved.
Audio generation is requested in **Italian**.

## Prerequisites

- Python 3.10+
- A desktop environment for the visible Chromium browser
- Tkinter for the local file picker (`/addfile`)
- A Google account with access to [NotebookLM](https://notebooklm.google.com/)
- The right to reproduce or process the specified source

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
```

The `dev` extra installs pytest. Use `pip install -e .` if you only need to run
the application.

## Running

```bash
./go.sh
```

The launcher uses the project's `.venv`, switches to the project directory, and
also works when invoked through a symbolic link. Alternatively, from the project
directory with the virtual environment activated:

```bash
news-to-audio
```

Chromium opens when a command needs NotebookLM. On first access, sign in to
Google **manually**. The session is stored in `state/browser-profile`, so later
runs normally do not require signing in again.

Set the output directory before your first synchronization:

```text
/output "~/Desktop/Podcasts"
/add https://example.com/article
/addfile
/list
/sync
```

`/addfile` opens a file picker. Canceling it leaves the queue unchanged.

## Terminal commands

| Command | Alias | Behavior |
| --- | --- | --- |
| `/add <url>` | `/a <url>` | Add a URL; duplicates are ignored. |
| `/addfile` | `/af` | Select a local file and add it to the queue. |
| `/list` | `/l` | Show pending URLs and files in insertion order. |
| `/sync` | `/s` | Check existing notebooks, download audio, delete `[DONE]` notebooks, and process the queue. |
| `/removeall` | `/r` | Clear the queue, including comments in its text file. |
| `/rejected` | `/scartati` | Show automatically rejected URLs. |
| `/output [directory]` | `/o [directory]` | Show or persistently change the output directory. Quote paths containing spaces. |
| `/help` | `/h`, `/?` | Show command help. |
| `/quit` | `/q`, `/exit` | Close the application. |

You can also paste a URL directly at the prompt. Addresses starting with `www.`
automatically use HTTPS. URL characters such as apostrophes, `&`, and `#` are
preserved; invalid addresses are rejected before entering the queue. Duplicate
detection ignores host/scheme casing, fragments, and trailing slashes.

## Synchronization

Each `/sync` runs these phases in order:

1. **Check existing notebooks.** Visit notebooks reachable from the NotebookLM
   home page, skipping titles starting with `[DONE]`. If an Audio Overview is
   missing, remove mind maps and start audio generation in Italian.
2. **Recover downloads.** Download ready Audio Overviews using locally stored
   source metadata. Reuse complete output or repair a previous bare MP3 when
   possible, then prefix the notebook title with `[DONE]`. Notebooks without
   the required source metadata or downloadable audio are reported and skipped.
3. **Delete completed notebooks.** Permanently delete notebooks whose titles
   start with `[DONE]`.
4. **Process the queue.** Handle URLs and local files in insertion order, waiting
   for each source import, audio generation, and download to finish. Save the
   required files, mark the notebook `[DONE]`, then remove the queue entry.
   Newly completed notebooks are deleted during a later `/sync`.

The initial check and cleanup apply to existing notebooks visible to the signed-in
account, including notebooks outside the local queue. The `[DONE]` prefix is the
deletion criterion; cleanup does not recheck local output files before deletion.

URLs are imported through **Add sources → Websites → Enter URLs**. This import
route also accepts YouTube links, but queued URLs must first pass the app's own
HTTP download and text extraction. YouTube transcript extraction is not implemented.
Local files are uploaded only when their queue position is reached; the app waits
for upload and analysis, including any spinner, before starting audio generation.

### Retries and interrupted work

- Inaccessible URLs move to the rejected list after three retrieval failures
  within the same `/sync` invocation.
- Other URL errors leave the item queued. Remaining URLs are retried in further
  passes, with a five-second pause between passes.
- A failed browser attempt can delete its newly created notebook and retry once
  from scratch. Local-file processing can resume an existing matching notebook.
- Any local-file error pauses queue processing and retains the item. Restore a
  moved or deleted file to its queued path, then run `/sync` again.
- Detection of NotebookLM's daily limit stops processing and preserves pending
  queue entries. Run `/sync` again when the allowance is available.

A final synchronization summary reports downloads, deleted notebooks, audio
started, completed items, remaining queue entries, rejected URLs, and notebooks
to review.

## Output files

| Source | Required output |
| --- | --- |
| URL | `.mp3`, `.txt` containing the URL, and `.md` containing the title, URL, and extracted text |
| Local file | `.mp3` and `.txt` containing the source's absolute path |
| Local PDF | The local-file output plus a copy of the original `.pdf` |

New notebook titles and output names use `YYYY.MM.DD Title`, with the processing
date as the prefix. Spaces are preserved and filename-incompatible characters
are replaced. Numeric suffixes such as `-2` avoid collisions; companion files
share the MP3's basename. Local files do not produce Markdown.

The interactive app stores the selected directory in `state/settings.json`.
Its current fallback is `~/Desktop/Overcast`; use `/output` to choose
a directory appropriate for your machine.

## Download existing audio separately

To recover ready audio without running the verification, deletion, or queue
processing phases:

```bash
news-to-audio-download --output-dir "./AudioOverview" --profile-dir "./state/browser-profile"
```

These are also the standalone command's default directories. It uses the same
source metadata and recovery logic as `/sync`, marks saved notebooks `[DONE]`,
and reports downloaded and skipped notebooks. It does not wait for generation
or create missing Audio Overviews. The interactive `/output` setting does not
change this command's default output directory.

## Formats and source access

URL extraction supports downloadable HTML, PDF, TXT/MD, DOCX, PPTX, and CSV.
A URL must be publicly retrievable and yield extractable text; scanned PDFs
without a text layer are not OCR-processed. Login- or paywall-protected content
is not bypassed. Local files are uploaded directly, so choose a format supported
by your NotebookLM account.

## Local state and diagnostics

Paths below are relative to the working directory. `./go.sh` ensures this is the
project directory.

| Path | Contents |
| --- | --- |
| `state/url-list.txt` | Pending queue: one URL or absolute `file://` URI per line; blank lines and `#` comments are allowed. |
| `state/rejected-urls.txt` | Automatically rejected URLs. Re-add a URL with `/add` to retry it. |
| `state/settings.json` | Selected interactive output directory. |
| `state/browser-profile/` | Persistent Chromium profile and Google session. |
| `state/source-urls.json` | Source references and text used to recover downloads. |
| `state/notebook-jobs.json` | Notebook titles and processing state. |

Browser errors save screenshots and HTML under the selected output directory's
`.debug` folder, with subfolders for failed imports and downloads. Verification
and deletion diagnostics use `AudioOverview/.debug`; some failures also write
`notebooklm-error.txt`. NotebookLM interaction uses browser automation, so changes
to its interface can require updates to the app.

## Development checks

Run the automated tests without interacting with a live NotebookLM account:

```bash
.venv/bin/python -B -m pytest
```

`scripts/e2e_smoke.py` is an explicit live smoke test: it uses a hard-coded public
URL, opens the saved browser session, and creates a test notebook and starts audio
generation. Run it only when you intend to exercise the account:

```bash
.venv/bin/python -B scripts/e2e_smoke.py
```
