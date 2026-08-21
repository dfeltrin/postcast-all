from news_to_audio.extract import _html_to_text


def test_html_extraction_prefers_article_text() -> None:
    page = b"<html><head><title>Test title</title></head><body><nav>Menu</nav><article><p>Hello article.</p><p>Useful detail.</p></article></body></html>"
    result = _html_to_text(page, "https://example.test/story")
    assert result.title == "Test title"
    assert "Hello article." in result.text
