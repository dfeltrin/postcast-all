"""Short-lived GUI process so native picker state cannot survive into sync."""

from __future__ import annotations

import json
import sys


def main() -> int:
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        selection = filedialog.askopenfilename(
            parent=root, title="Seleziona un file da aggiungere a NotebookLM"
        )
        print(json.dumps(selection))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
