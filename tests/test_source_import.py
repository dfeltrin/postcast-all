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
                <section role="dialog" hidden>
                    <textarea formcontrolname="copiedText" aria-label="Testo incollato"></textarea>
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


def test_pasted_text_waits_for_dialog_and_never_fills_chat(monkeypatch):
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <textarea aria-label="Scopri le fonti in base alla query immessa"></textarea>
                <button onclick="setTimeout(() => document.querySelector('section').hidden = false, 800)">Testo copiato</button>
                <section role="dialog" hidden>
                    <textarea formcontrolname="copiedText" aria-label="Testo incollato"></textarea>
                    <button onclick="document.querySelector('output').textContent = document.querySelector('[formcontrolname=copiedText]').value">Inserisci</button>
                </section>
                <textarea aria-label="Casella di query"></textarea>
                <button onclick="throw Error('Wrong submit')">Inserisci</button>
                <output></output>
            ''')
            monkeypatch.setattr(notebooklm, '_click_first', lambda *a, **kw: None)
            monkeypatch.setattr(notebooklm, '_set_notebook_title', lambda *a: None)
            monkeypatch.setattr(notebooklm, '_wait_for_copied_text_ready', lambda *a: None)
            notebooklm._add_copied_text(page, 'Example', 'https://example.com', 'Article text', lambda m: None)
            assert page.locator('output').text_content() == 'Source: https://example.com\n\nArticle text'
            assert page.get_by_label('Casella di query').input_value() == ''
            assert page.get_by_label('Scopri le fonti in base alla query immessa').input_value() == ''
        finally:
            browser.close()


@pytest.mark.parametrize('label', ['Siti web', 'Websites'])
def test_website_import_pastes_only_url_into_website_dialog(monkeypatch, label):
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f'''
                <textarea aria-label="Casella di query"></textarea>
                <button onclick="throw Error('Wrong source type')">Testo copiato</button>
                <button onclick="setTimeout(() => document.querySelector('section').hidden = false, 500)">{label}</button>
                <section role="dialog" hidden>
                    <textarea formcontrolname="urls" aria-label="Inserisci gli URL"></textarea>
                    <button onclick="document.querySelector('output').textContent = document.querySelector('[formcontrolname=urls]').value">Inserisci</button>
                </section>
                <textarea aria-label="Cerca nuove fonti sul web"></textarea>
                <button onclick="throw Error('Wrong submit')">Inserisci</button>
                <output></output>
            ''')
            monkeypatch.setattr(notebooklm, '_click_first', lambda *a, **kw: None)
            monkeypatch.setattr(notebooklm, '_set_notebook_title', lambda *a: None)
            analyzed = []
            monkeypatch.setattr(notebooklm, '_wait_for_copied_text_ready', lambda *a: analyzed.append(True))
            url = 'https://youtu.be/Iby_rZHtX7w?si=si48obPCmjdM-OGl'
            notebooklm._add_website(page, 'Example', url, lambda m: None)
            assert page.locator('output').text_content() == url
            assert page.get_by_label('Casella di query').input_value() == ''
            assert page.get_by_label('Cerca nuove fonti sul web').input_value() == ''
            assert analyzed == [True]
        finally:
            browser.close()
