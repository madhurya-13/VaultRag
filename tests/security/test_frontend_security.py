"""Frontend XSS checks: static guard plus the rendered Vitest exploit case."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_chat_page_does_not_use_raw_html_insertion():
    source = (
        REPOSITORY_ROOT / "frontend" / "src" / "pages" / "chat-page.tsx"
    ).read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in source
