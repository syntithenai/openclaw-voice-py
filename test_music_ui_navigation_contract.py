from pathlib import Path

from orchestrator.gateway.quick_answer import classify_upstream_decision
from orchestrator.music.parser import MusicFastPathParser


def test_queue_queries_are_classified_as_music_related() -> None:
    parser = MusicFastPathParser()

    assert parser.is_music_related("change what is in the queue")
    assert parser.is_music_related("load the queued playlist")


def test_queue_queries_stay_on_local_music_path() -> None:
    should_use_upstream, reason = classify_upstream_decision(
        "change what is in the queue",
        music_enabled=True,
    )

    assert not should_use_upstream
    assert reason == "music_local"


def test_music_page_navigation_uses_broader_music_intent() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "def _should_navigate_to_music_page(" in source
    assert 're.search(r"\\b(queue|playlist)\\b", normalized)' in source
    assert '"music" if _should_navigate_to_music_page(combined_transcript, parsed_music, is_music_query) else "home"' in source