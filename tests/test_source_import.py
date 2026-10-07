import re

import pytest

from news_to_audio import notebooklm


@pytest.mark.parametrize('already_open', [True, False])
@pytest.mark.parametrize('label', ['Testo copiato', 'Copied text', 'Carica file', 'Upload files'])
def test_source_options_without_legacy_css(already_open, label):
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f'''
                <button aria-label="Aggiungi fonte"
                    onclick="document.querySelector('[role=dialog]').hidden = false;
                             this.dataset.clicked = 'true'">Aggiungi fonti</button>
                <button hidden>{label}</button>
                <section role="dialog" {'hidden' if not already_open else ''}>
                    <button onclick="this.dataset.clicked = 'true'">{label}</button>
                </section>
            ''')
            option = notebooklm._source_import_option(page, re.compile(re.escape(label), re.I))
            option.click()
            assert option.get_attribute('data-clicked') == 'true'
            assert page.get_by_label('Aggiungi fonte').get_attribute('data-clicked') == (
                None if already_open else 'true'
            )
        finally:
            browser.close()


def test_pasted_text_import_ignores_background_add_source(monkeypatch):
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <button>Aggiungi fonti</button>
                <button onclick="document.querySelector('section').hidden = false">Testo copiato</button>
                <section hidden>
                    <textarea></textarea>
                    <button onclick="document.querySelector('output').textContent = document.querySelector('textarea').value">Inserisci</button>
                </section>
                <output></output>
            ''')
            monkeypatch.setattr(notebooklm, '_click_first', lambda *a, **kw: None)
            monkeypatch.setattr(notebooklm, '_set_notebook_title', lambda *a: None)
            monkeypatch.setattr(notebooklm, '_wait_for_copied_text_ready', lambda *a: None)
            notebooklm._add_copied_text(page, 'Example', 'https://example.com', 'Article text', lambda m: None)
            assert page.locator('output').text_content() == 'Source: https://example.com\n\nArticle text'
        finally:
            browser.close()
