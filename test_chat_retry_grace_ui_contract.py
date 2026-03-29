from pathlib import Path


def test_chat_retry_grace_failure_banner_and_partial_stream_contract() -> None:
    source = Path("orchestrator/web/static/app-events.js").read_text(encoding="utf-8")

    assert "TRANSIENT_LIFECYCLE_ERROR_GRACE_MS=15000" in source
    assert "Run failed after retry grace expired." in source
    assert "connection dropped, waiting for retry grace" in source
    assert "if(validStreams.length>0 && !hasTextFinal)" in source
    assert "scheduleLifecycleGraceRerender" in source