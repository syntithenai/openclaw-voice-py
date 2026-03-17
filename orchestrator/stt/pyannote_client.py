import requests


class PyannoteClient:
    def __init__(self, base_url: str, model_id: str = "pyannote/speaker-diarization-3.1") -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = (model_id or "pyannote/speaker-diarization-3.1").strip()

    def diarize(self, wav_bytes: bytes) -> list[dict]:
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model_id": self.model_id}
        response = requests.post(f"{self.base_url}/diarize", files=files, data=data, timeout=300)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"Pyannote backend error: {payload['error']}")

        rows = payload.get("segments", []) or []
        normalized = []
        for row in rows:
            normalized.append(
                {
                    "start": float(row.get("start", 0.0) or 0.0),
                    "end": float(row.get("end", 0.0) or 0.0),
                    "speaker": str(row.get("speaker", "UNKNOWN")),
                }
            )
        return normalized
