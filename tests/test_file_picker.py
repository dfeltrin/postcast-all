import os
from pathlib import Path

import pytest

from news_to_audio import cli


@pytest.mark.parametrize("outcome", ["selected", "cancelled", "error"])
def test_picker_process_exits_before_cli_continues(tmp_path, monkeypatch, capsys, outcome):
    """Exercise the real child process without opening a desktop dialog."""
    selected = tmp_path / "Una città 'speciale'.pdf"
    exited = tmp_path / "picker-exited"
    (tmp_path / "tkinter.py").write_text(
        '''
import atexit
import os
from pathlib import Path

destroyed = False

def record_exit():
    if destroyed:
        Path(os.environ["PICKER_EXIT_MARKER"]).touch()

atexit.register(record_exit)

class Tk:
    def withdraw(self): pass
    def attributes(self, *args): pass
    def update_idletasks(self): pass
    def destroy(self):
        global destroyed
        destroyed = True

class filedialog:
    @staticmethod
    def askopenfilename(**kwargs):
        outcome = os.environ["PICKER_OUTCOME"]
        if outcome == "error":
            raise RuntimeError("picker unavailable")
        return os.environ["PICKER_SELECTION"] if outcome == "selected" else ""
''', encoding="utf-8"
    )
    source_dir = Path(cli.__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(tmp_path), str(source_dir)]))
    monkeypatch.setenv("PICKER_EXIT_MARKER", str(exited))
    monkeypatch.setenv("PICKER_OUTCOME", outcome)
    monkeypatch.setenv("PICKER_SELECTION", str(selected))

    result = cli._pick_file()

    assert result == (selected if outcome == "selected" else None)
    assert exited.exists(), "The GUI process must finish cleanup before returning"
    if outcome == "error":
        assert "picker unavailable" in capsys.readouterr().out
