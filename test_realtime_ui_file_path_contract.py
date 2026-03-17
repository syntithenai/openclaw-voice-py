from pathlib import Path


def test_tool_request_extracts_snake_case_file_path() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri" in source


def test_thinking_block_shows_waiting_icon_in_summary() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "const thinkingSummary=waiting" in source
    assert "animate-spin" in source


def test_exec_preview_clamped_to_two_lines() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "const clampPreviewLines=(raw, maxLines=2)=>" in source
    assert "clampPreviewLines(execCommand, 2)" in source


def test_transient_lifecycle_errors_not_auto_terminal_failure() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "const isTransientLifecycleError=(phase, errText)=>" in source
    assert "const hasLifecycleError=hasLifecycleHardError" in source
