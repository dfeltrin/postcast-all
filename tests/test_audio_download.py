import pytest

from news_to_audio import notebooklm


@pytest.fixture
def page():
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        yield page
        browser.close()


@pytest.mark.parametrize('description', ['Overview audio', 'Audio overview'])
def test_wait_recognizes_completion_after_spinner(page, monkeypatch, description):
    page.set_content(f'''
        <artifact-library-item>
          <button aria-description="{description}" disabled>Preparing</button>
          <mat-icon>progress_activity</mat-icon>
        </artifact-library-item>
        <artifact-library-item><mat-icon>flowchart</mat-icon>Map</artifact-library-item>
    ''')
    assert notebooklm._has_audio_overview(page)
    assert not notebooklm._audio_item_ready(notebooklm._audio_overview_items(page)[0])
    monkeypatch.setattr(notebooklm, 'GENERATION_POLL_MS', 20)
    page.evaluate('''() => setTimeout(() => {
        document.querySelector('button').disabled = false;
        document.querySelector('mat-icon').textContent = 'headphones';
    }, 80)''')
    notebooklm._wait_for_audio_ready(page, 'Test', lambda message: None)


@pytest.mark.parametrize('save', [notebooklm._save_download, notebooklm._save_ready_audio])
def test_download_targets_ready_audio_not_other_artifacts(page, tmp_path, save):
    page.set_content('''
        <artifact-library-item>
          <button aria-description="Overview audio">Ready audio</button>
          <mat-icon>headphones</mat-icon>
          <button aria-haspopup="menu" onclick="document.querySelector('[role=menu]').hidden=false">Altro</button>
        </artifact-library-item>
        <artifact-library-item>
          <button aria-description="Overview audio" disabled>Preparing</button>
          <mat-icon>progress_activity</mat-icon>
        </artifact-library-item>
        <artifact-library-item>
          <mat-icon>flowchart</mat-icon>
          <button class="artifact-more-button" onclick="throw Error('Wrong artifact')">Altro</button>
          <button onclick="throw Error('Wrong download')">Scarica</button>
        </artifact-library-item>
        <div role="menu" hidden>
          <a role="menuitem" download="audio.mp3" href="data:audio/mpeg;base64,SUQzYXVkaW8=">Scarica</a>
        </div>
    ''')
    output = save(page, 'Test', tmp_path, lambda message: None)
    assert output.read_bytes() == b'ID3audio'


def test_generating_audio_is_not_downloaded(page, tmp_path):
    page.set_content('''<artifact-library-item>
        <button aria-description="Audio overview" disabled>Preparing</button>
        <mat-icon>progress_activity</mat-icon>
    </artifact-library-item>''')
    with pytest.raises(notebooklm.NotebookLMError, match='still generating'):
        notebooklm._save_ready_audio(page, 'Test', tmp_path, lambda message: None)
    assert not list(tmp_path.iterdir())


def test_download_waits_for_studio_library(page, tmp_path):
    page.set_content("<main>Loading Studio</main>")
    page.evaluate('''() => setTimeout(() => {
        document.querySelector('main').innerHTML = `
          <artifact-library-item>
            <button aria-description="Overview audio">Ready</button>
            <mat-icon>audio_spark</mat-icon>
            <a role="button" download="audio.mp3"
               href="data:audio/mpeg;base64,SUQzYXVkaW8=">Scarica</a>
          </artifact-library-item>`;
    }, 500)''')
    output = notebooklm._save_download(page, 'Delayed', tmp_path, lambda message: None)
    assert output.read_bytes() == b'ID3audio'
