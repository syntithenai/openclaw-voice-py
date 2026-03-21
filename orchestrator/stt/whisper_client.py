import requests
from dataclasses import dataclass


@dataclass
class WhisperTranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class WhisperTranscriptResult:
    text: str
    segments: list[WhisperTranscriptSegment]
    language: str = ""


class WhisperClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def transcribe_detailed(self, wav_bytes: bytes) -> WhisperTranscriptResult:
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        response = requests.post(f"{self.base_url}/transcribe", files=files, timeout=120)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"Whisper backend error: {payload['error']}")
        segments_payload = payload.get("segments") or []
        segments = [
            WhisperTranscriptSegment(
                start=float(item.get("start", 0.0) or 0.0),
                end=float(item.get("end", 0.0) or 0.0),
                text=str(item.get("text", "") or "").strip(),
            )
            for item in segments_payload
        ]
        return WhisperTranscriptResult(
            text=str(payload.get("text", "") or ""),
            segments=segments,
            language=str(payload.get("language", "") or ""),
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        return self.transcribe_detailed(wav_bytes).text
