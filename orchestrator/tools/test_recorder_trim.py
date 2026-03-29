from orchestrator.tools.recorder import compute_hotword_stop_trim_seconds


def test_compute_hotword_stop_trim_seconds_adds_padding() -> None:
    trim = compute_hotword_stop_trim_seconds(
        armed_ts=10.0,
        stop_ts=11.5,
        extra_trim_ms=900,
        max_trim_ms=8000,
    )
    assert trim == 2.4


def test_compute_hotword_stop_trim_seconds_caps_maximum() -> None:
    trim = compute_hotword_stop_trim_seconds(
        armed_ts=10.0,
        stop_ts=30.0,
        extra_trim_ms=900,
        max_trim_ms=8000,
    )
    assert trim == 8.0


def test_compute_hotword_stop_trim_seconds_handles_missing_timestamps() -> None:
    assert compute_hotword_stop_trim_seconds(armed_ts=None, stop_ts=12.0) == 0.0
    assert compute_hotword_stop_trim_seconds(armed_ts=10.0, stop_ts=None) == 0.0


def test_compute_hotword_stop_trim_seconds_falls_back_to_padding_when_stop_precedes_arm() -> None:
    trim = compute_hotword_stop_trim_seconds(
        armed_ts=12.0,
        stop_ts=11.0,
        extra_trim_ms=900,
        max_trim_ms=8000,
    )
    assert trim == 0.9