from pathlib import Path


def test_realtime_service_source_compiles() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    compile(source, "orchestrator/web/realtime_service.py", "exec")