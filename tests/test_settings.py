from pathlib import Path

from news_to_audio.settings import DEFAULT_OUTPUT_DIR, load_output_dir, save_output_dir


def test_default_output_dir_is_overcast(tmp_path) -> None:
    assert load_output_dir(tmp_path / "missing.json") == DEFAULT_OUTPUT_DIR


def test_output_dir_is_saved_and_loaded(tmp_path) -> None:
    settings_file = tmp_path / "state" / "settings.json"
    expected = Path("/tmp/audio-output")
    save_output_dir(expected, settings_file)
    assert load_output_dir(settings_file) == expected
