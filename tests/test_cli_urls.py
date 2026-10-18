import pytest

from news_to_audio import cli
from news_to_audio.url_list import active_urls, add_url


@pytest.mark.parametrize("line, expected", [
    ("/add https://example.com/what's-new?x=1&y=2", "https://example.com/what's-new?x=1&y=2"),
    ('/a "https://example.com/path?q=value"', "https://example.com/path?q=value"),
    ("HTTPS://example.com/article", "HTTPS://example.com/article"),
    ("www.example.com/article", "https://www.example.com/article"),
    ("/add https://example.com/?q=%20#section", "https://example.com/?q=%20#section"),
])
def test_cli_preserves_pasted_urls(tmp_path, monkeypatch, line, expected):
    queue = tmp_path / "queue.txt"
    monkeypatch.setattr(cli, "add_url", lambda value: add_url(value, queue))
    inputs = iter([line, "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    cli.main()
    assert active_urls(queue) == [expected]


@pytest.mark.parametrize("line", [
    "/add", "/add https://", "/add https://example.com:invalid/",
    "/add https://[invalid/", "/add https://example.com/ two",
    "/add ftp://example.com/file", "/add https://example.com:99999/",
    "/add https://example.com/\x00", "/", "/   ",
])
def test_bad_input_does_not_crash_or_enqueue(monkeypatch, line, capsys):
    def unexpected_enqueue(value):
        pytest.fail(f"Invalid URL was queued: {value}")
    monkeypatch.setattr(cli, "add_url", unexpected_enqueue)
    inputs = iter([line, "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    cli.main()
    assert "Goodbye." in capsys.readouterr().out
