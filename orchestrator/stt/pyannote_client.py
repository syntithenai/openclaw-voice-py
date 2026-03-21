import requests
from pathlib import Path


class PyannoteClient:
    def __init__(self, base_url: str, model_id: str = "pyannote/speaker-diarization-3.1", auth_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = (model_id or "pyannote/speaker-diarization-3.1").strip()
        self.auth_token = (auth_token or "").strip()

    def diarize(self, wav_bytes: bytes) -> list[dict]:
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        model_id_to_send = self.model_id
        if model_id_to_send:
            looks_like_fs_path = (
                model_id_to_send.startswith(("/", "./", "../", "~"))
                or Path(model_id_to_send).is_absolute()
                or "\\" in model_id_to_send
            )
            if looks_like_fs_path:
                model_id_to_send = ""

        data = {}
        if model_id_to_send:
            data["model_id"] = model_id_to_send
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
            data["auth_token"] = self.auth_token
        response = requests.post(
            f"{self.base_url}/diarize",
            files=files,
            data=data,
            headers=headers,
            timeout=300,
        )
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
