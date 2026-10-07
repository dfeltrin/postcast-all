from unittest.mock import MagicMock

import pytest

from news_to_audio import notebooklm


@pytest.mark.parametrize("remaining", [[], ["Keep this notebook"]])
def test_cleanup_waits_for_rows_after_each_reload(monkeypatch, tmp_path, remaining):
    titles = ["[DONE] First", "[DONE] Second", "[DONE] Third", *remaining]
    page = MagicMock()
    loaded = False
    selected = None

    def navigate(*args, **kwargs):
        nonlocal loaded
        loaded = False

    def wait_for_rows(**kwargs):
        nonlocal loaded
        loaded = True
        if not titles:
            raise notebooklm.PlaywrightTimeoutError("Empty notebook list")

    cells = MagicMock()
    cells.first.wait_for.side_effect = wait_for_rows
    cells.count.side_effect = lambda: len(titles) if loaded else 0

    def cell_at(index):
        cell = MagicMock()
        title = titles[index]
        cell.get_attribute.return_value = title

        def select(**kwargs):
            nonlocal selected
            selected = title

        cell.locator.return_value.locator.return_value.first.click.side_effect = select
        return cell

    cells.nth.side_effect = cell_at
    page.locator.return_value = cells
    page.goto.side_effect = navigate
    list_view = MagicMock()
    list_view.count.return_value = 1
    page.get_by_label.return_value.filter.return_value = list_view
    confirm = MagicMock()
    confirm.last.click.side_effect = lambda: titles.remove(selected)

    def get_by_role(role, **kwargs):
        if role == "button" and kwargs["name"].pattern.startswith("^elimina"):
            return confirm
        return list_view

    page.get_by_role.side_effect = get_by_role
    playwright = MagicMock()
    context = playwright.__enter__.return_value.chromium.launch_persistent_context.return_value
    context.pages = [page]
    monkeypatch.setattr(notebooklm, "sync_playwright", lambda: playwright)
    monkeypatch.setattr(notebooklm, "_wait_for_signin_or_home", lambda page, report: navigate())

    assert notebooklm.delete_done_notebooks(tmp_path, on_status=lambda message: None) == 3
    assert titles == remaining
    assert page.goto.call_count == 3
    context.close.assert_called_once()


@pytest.mark.parametrize("role", ["radio", "button"])
@pytest.mark.parametrize("label", ["Visualizzazione elenco", "List view"])
def test_list_view_toggle_uses_accessible_label(role, label):
    with notebooklm.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f'''
                <button role="{role}" aria-label="{label}" style="display:none"></button>
                <button role="{role}" aria-label="{label}"
                    onclick="document.querySelector('table').hidden = false">view_headline</button>
                <table hidden><tr role="row"><td>
                    <a class="project-table-title" title="[DONE] Example">[DONE] Example</a>
                </td></tr></table>
            ''')
            notebooklm._select_notebook_list_view(page)
            assert page.locator('.project-table-title').is_visible()
        finally:
            browser.close()
