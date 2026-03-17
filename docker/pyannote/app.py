import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, UploadFile, Form

app = FastAPI()

MODEL_ID = os.getenv("PYANNOTE_MODEL_ID", "pyannote/speaker-diarization-3.1").strip()
BACKEND_PREFERENCE = os.getenv("PYANNOTE_BACKEND_PREFERENCE", "auto").strip().lower() or "auto"
CPU_FALLBACK_ENABLED = os.getenv("PYANNOTE_CPU_FALLBACK", "true").strip().lower() not in {"0", "false", "no"}
AUTH_TOKEN = os.getenv("PYANNOTE_AUTH_TOKEN", "").strip()

_PIPELINE = None
_PIPELINE_MODEL = None
_PIPELINE_DEVICE = "unknown"


def _resolve_device() -> tuple[str, bool]:
    import torch

    gpu_available = bool(torch.cuda.is_available())

    if BACKEND_PREFERENCE == "cpu":
        return "cpu", gpu_available
    if BACKEND_PREFERENCE == "gpu":
        return "cuda", gpu_available

    return ("cuda" if gpu_available else "cpu"), gpu_available


def _load_pipeline(model_id: str):
    global _PIPELINE, _PIPELINE_MODEL, _PIPELINE_DEVICE

    if _PIPELINE is not None and _PIPELINE_MODEL == model_id:
        return _PIPELINE

    model_ref = Path(model_id)
    use_local_model = model_ref.exists()
    if not AUTH_TOKEN and not use_local_model:
        raise RuntimeError("PYANNOTE_AUTH_TOKEN is missing")

    from pyannote.audio import Pipeline

    target_device, _gpu_available = _resolve_device()

    if use_local_model:
        pipeline = Pipeline.from_pretrained(str(model_ref))
    else:
        pipeline = Pipeline.from_pretrained(model_id, use_auth_token=AUTH_TOKEN)

    if target_device == "cuda":
        try:
            import torch

            pipeline = pipeline.to(torch.device("cuda"))
            _PIPELINE_DEVICE = "gpu"
        except Exception:
            if not CPU_FALLBACK_ENABLED:
                raise
            _PIPELINE_DEVICE = "cpu"
    else:
        _PIPELINE_DEVICE = "cpu"

    _PIPELINE = pipeline
    _PIPELINE_MODEL = model_id
    return _PIPELINE


@app.get("/health")
async def health() -> dict[str, Any]:
    import torch

    _target_device, gpu_available = _resolve_device()
    return {
        "status": "ok",
        "model": MODEL_ID,
        "backend_preference": BACKEND_PREFERENCE,
        "cpu_fallback_enabled": CPU_FALLBACK_ENABLED,
        "gpu_available": gpu_available,
        "active_device": _PIPELINE_DEVICE,
        "token_configured": bool(AUTH_TOKEN),
        "cuda_device_count": int(torch.cuda.device_count()) if gpu_available else 0,
    }


@app.post("/diarize")
async def diarize(file: UploadFile, model_id: str = Form(default="")) -> dict[str, Any]:
    audio = await file.read()
    if not audio:
        return {"segments": [], "error": "empty audio"}

    resolved_model = (model_id or MODEL_ID).strip() or MODEL_ID

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
        temp_audio.write(audio)
        temp_audio_path = temp_audio.name

    try:
        pipeline = _load_pipeline(resolved_model)
        diarization = pipeline(temp_audio_path)

        segments: list[dict[str, Any]] = []
        for segment, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "speaker": str(speaker),
                }
            )
        segments.sort(key=lambda row: (row["start"], row["end"]))

        return {
            "segments": segments,
            "model": resolved_model,
            "backend": _PIPELINE_DEVICE,
        }
    except Exception as exc:
        return {
            "segments": [],
            "error": str(exc),
            "model": resolved_model,
            "backend": _PIPELINE_DEVICE,
        }
    finally:
        try:
            Path(temp_audio_path).unlink(missing_ok=True)
        except Exception:
            pass
