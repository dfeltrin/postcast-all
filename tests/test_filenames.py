from datetime import date

from news_to_audio.notebooklm import (
    _audio_candidates,
    _daily_limit_message,
    _done_title,
    _is_complete_audio,
    _forget_notebook_job,
    _load_notebook_jobs,
    _publish_audio_bundle,
    _publish_missing_sidecars,
    _remember_notebook_job,
    _safe_filename,
    _source_text,
    _source_url,
    _write_source_md,
    _write_source_txt,
    normalize_title,
    record_source_url,
)
from news_to_audio.url_list import active_urls, add_file, add_url, count_urls, discard_url, first_url, remove_all, remove_url


def test_download_name_preserves_the_notebook_title() -> None:
    assert _safe_filename("Titolo del Notebook: prova") == "Titolo del Notebook- prova"


def test_url_sidecar_uses_the_same_name_as_the_audio(tmp_path) -> None:
    record_source_url(tmp_path, "Titolo", "https://example.test/source", "Testo della fonte")
    audio = tmp_path / "Titolo.mp3"
    sidecar = _write_source_txt(audio, _source_url(tmp_path, "Titolo") or "")
    assert sidecar.name == "Titolo.txt"
    assert sidecar.read_text() == "https://example.test/source\n"
    markdown = _write_source_md(audio, "Titolo", _source_url(tmp_path, "Titolo") or "", _source_text(tmp_path, "Titolo") or "")
    assert markdown.name == "Titolo.md"
    assert "Testo della fonte" in markdown.read_text()


def test_local_file_sidecar_keeps_the_full_path(tmp_path) -> None:
    source = tmp_path / "documento.pdf"
    audio = tmp_path / "Titolo.mp3"

    sidecar = _write_source_txt(audio, str(source.resolve()))

    assert sidecar.read_text() == f"{source.resolve()}\n"


def test_recording_a_pending_source_does_not_create_markdown(tmp_path) -> None:
    record_source_url(tmp_path, "Titolo", "https://example.test/source", "Testo della fonte")
    assert not (tmp_path / "Titolo.md").exists()
    assert not (tmp_path / ".source-urls.json").exists()


def test_done_prefix_is_added_once() -> None:
    assert _done_title("Titolo") == "[DONE] Titolo"
    assert _done_title("[DONE] Titolo") == "[DONE] Titolo"


def test_title_is_date_prefixed_and_file_safe() -> None:
    assert normalize_title('Introduzione: Agent/Tools', date(2025, 11, 26)) == "2025.11.26 Introduzione- Agent-Tools"


def test_publish_bundle_creates_all_artifacts_with_matching_names(tmp_path) -> None:
    staging = tmp_path / ".pending"
    staging.mkdir()
    staged_audio = staging / "Titolo.mp3"
    staged_audio.write_bytes(b"mp3 data")

    audio = _publish_audio_bundle(staged_audio, tmp_path, "Titolo", "https://example.test/source", "Testo della fonte")

    assert audio == tmp_path / "Titolo.mp3"
    assert _is_complete_audio(audio)
    assert audio.with_suffix(".txt").read_text() == "https://example.test/source\n"
    assert "Testo della fonte" in audio.with_suffix(".md").read_text()


def test_local_file_bundle_does_not_create_markdown(tmp_path) -> None:
    staging = tmp_path / ".pending"
    staging.mkdir()
    staged_audio = staging / "Titolo.mp3"
    staged_audio.write_bytes(b"mp3 data")

    source = tmp_path / "documento.PDF"
    source.write_bytes(b"original pdf content")
    (tmp_path / "Titolo.mp3").write_bytes(b"existing audio")
    audio = _publish_audio_bundle(
        staged_audio, tmp_path, "Titolo", str(source), "", include_markdown=False
    )

    assert audio.name == "Titolo-2.mp3"
    assert audio.with_suffix(".txt").read_text() == f"{source}\n"
    assert audio.with_suffix(".pdf").read_bytes() == source.read_bytes()
    assert not audio.with_suffix(".md").exists()
    assert _is_complete_audio(audio, include_markdown=False)


def test_existing_bare_audio_can_be_completed_without_a_second_download(tmp_path) -> None:
    audio = tmp_path / "Titolo.mp3"
    audio.write_bytes(b"mp3 data")

    _publish_missing_sidecars(audio, "Titolo", "https://example.test/source", "Testo della fonte")

    assert _is_complete_audio(audio)
    assert _audio_candidates(tmp_path, "Titolo") == [audio]


def test_daily_limit_messages_are_recognized_in_italian_and_english() -> None:
    assert _daily_limit_message("Hai raggiunto il limite giornaliero per NotebookLM.")
    assert _daily_limit_message("Hai raggiunto il limite giornaliero di overview audio.")
    assert _daily_limit_message("You've reached your daily limit. Try again tomorrow.")
    assert _daily_limit_message("Generazione completata") is None


def test_notebook_job_registry_reuses_the_same_url_across_retry_variations(tmp_path) -> None:
    jobs_file = tmp_path / "notebook-jobs.json"
    _remember_notebook_job("https://EXAMPLE.test/article/#section", "Titolo", "created", jobs_file)
    jobs = _load_notebook_jobs(jobs_file)
    assert jobs["https://example.test/article"]["title"] == "Titolo"
    _forget_notebook_job("https://example.test/article", jobs_file)
    assert _load_notebook_jobs(jobs_file) == {}


def test_url_list_is_plain_text_and_does_not_duplicate_urls(tmp_path) -> None:
    list_file = tmp_path / "url-list.txt"
    assert add_url("https://example.test/article", list_file)
    assert not add_url("https://example.test/article", list_file)
    assert not add_url("https://EXAMPLE.test/article/#commento", list_file)
    assert list_file.read_text() == "https://example.test/article\n"


def test_url_list_processes_and_removes_only_the_first_active_url(tmp_path) -> None:
    list_file = tmp_path / "url-list.txt"
    list_file.write_text("# Da elaborare\n\nhttps://example.test/first\nhttps://example.test/second\n")
    assert count_urls(list_file) == 2
    assert active_urls(list_file) == ["https://example.test/first", "https://example.test/second"]
    assert first_url(list_file) == "https://example.test/first"
    assert remove_url("https://example.test/first", list_file)
    assert count_urls(list_file) == 1
    assert list_file.read_text() == "# Da elaborare\n\nhttps://example.test/second\n"


def test_local_file_is_stored_in_the_same_plain_text_queue(tmp_path) -> None:
    list_file = tmp_path / "url-list.txt"
    source = tmp_path / "documento.pdf"
    source.write_bytes(b"pdf")

    assert add_url("https://example.test/first", list_file)
    assert add_file(source, list_file)
    assert not add_file(source, list_file)
    assert active_urls(list_file) == ["https://example.test/first", source.resolve().as_uri()]


def test_remove_all_clears_the_entire_queue(tmp_path) -> None:
    list_file = tmp_path / "url-list.txt"
    list_file.write_text("# nota\nhttps://example.test/first\nhttps://example.test/second\n")

    assert remove_all(list_file) == 2
    assert list_file.read_text() == ""
    assert active_urls(list_file) == []


def test_discarded_url_is_moved_once_to_its_own_plain_text_list(tmp_path) -> None:
    list_file = tmp_path / "url-list.txt"
    rejected_file = tmp_path / "rejected-urls.txt"
    add_url("https://example.test/blocked", list_file)

    assert discard_url("https://example.test/blocked", list_file, rejected_file)
    assert active_urls(list_file) == []
    assert active_urls(rejected_file) == ["https://example.test/blocked"]
