"""フッターのクレジット（やねうら王の GitHub・Wiki、水匠5の作者と配布元）。"""
from shogi_analyzer import credits


def test_footer_has_engine_links():
    text = credits.footer_markdown()
    assert "やねうら王" in text and "GPL-3.0" in text
    assert "(" + credits.YANEURAOU_GITHUB + ")" in text
    assert "(" + credits.YANEURAOU_WIKI + ")" in text
    assert "水匠5" in text and "たややん" in text and "(" + credits.SUISHO5_RELEASE + ")" in text
    assert "(" + credits.SOURCE_URL + ")" in text


def test_commit_matches_dockerfiles():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for f in ("Dockerfile", "engine_service/Dockerfile"):
        assert "YANEURAOU_COMMIT=" + credits.YANEURAOU_COMMIT in (root / f).read_text()


def test_sidebar_has_github_and_wiki():
    text = credits.sidebar_markdown()
    assert credits.YANEURAOU_GITHUB in text and credits.YANEURAOU_WIKI in text
