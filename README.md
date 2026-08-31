# Podcast All

Local agent for creating NotebookLM Audio Overviews from URLs or local files,
with a persistent queue. An item is removed from the queue only after its audio
has been downloaded successfully.

- For a URL, it creates an MP3, TXT file (the source URL), and Markdown file
  (the extracted text).
- For a local file, it creates an MP3 and TXT file (the file's absolute path);
  it does not create Markdown.

## Prerequisites

- Python 3.10+
- A Google account with access to [NotebookLM](https://notebooklm.google.com/)
- The right to reproduce or process the specified source

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
```

## Running

```bash
./go.sh
```

Alternatively, after installation:

```bash
news-to-audio
```

On the first run, Chromium opens: sign in to Google **manually**. The session
is stored in `state/browser-profile`, so subsequent runs normally do not
require signing in again.

## Terminal queue

Main commands:

- `/add <url>` or `/a <url>`: adds a URL to the queue.
- `/addfile` or `/af`: opens the system file picker and adds a local file.
- `/list` or `/l`: shows pending URLs and files.
- `/sync` or `/s`: processes the queue in insertion order.
- `/removeall` or `/r`: clears the entire queue.
- `/rejected`: shows URLs that were automatically rejected.
- `/output [directory]` or `/o [directory]`: displays or sets the output directory.

With `/addfile`, the file is saved to the queue alongside URLs rather than
being sent to NotebookLM immediately. During `/sync`, it is uploaded only when
its turn is reached. The app waits for the upload and analysis to finish
(including any spinner) before starting the Audio Overview.

If a file is moved or deleted before processing, it remains in the queue and
synchronization stops without retrying in a loop. Once the issue is fixed, run
`/sync` again: the app resumes the notebook it already created.

## Formats

For URLs, it supports downloadable HTML, PDF, TXT/MD, DOCX, PPTX, and CSV
files. For local files, choose a format supported by your NotebookLM account.
Login- or paywall-protected content is not bypassed.

## Diagnostics

Interaction with NotebookLM takes place in the browser. On error, screenshots
and HTML are saved to `output/.debug`.
