import os
import tempfile
import subprocess
import json
from pathlib import Path

from fastapi import FastAPI, UploadFile

app = FastAPI()

WHISPER_CPP_PATH = os.getenv("WHISPER_CPP_PATH", "/app/build/bin/whisper-cli")
MODEL_PATH = os.getenv("MODEL_PATH", "/models/ggml-base.en.bin")
BACKEND_PREFERENCE = os.getenv("WHISPER_BACKEND_PREFERENCE", "auto").strip().lower() or "auto"
CPU_FALLBACK_ENABLED = os.getenv("WHISPER_CPU_FALLBACK", "true").strip().lower() not in {"0", "false", "no"}


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _parse_timestamp_text(value: str) -> float:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return 0.0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return (hours * 3600.0) + (minutes * 60.0) + seconds
        if len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return (minutes * 60.0) + seconds
        return float(raw)
    except Exception:
        return 0.0


def _normalize_segments(output: dict) -> list[dict]:
    rows = output.get("transcription") or []
    normalized: list[dict] = []
    for row in rows:
        text = str(row.get("text", "") or "").strip()
        offsets = row.get("offsets") or {}
        timestamps = row.get("timestamps") or {}

        start = None
        end = None

        if "from" in offsets or "to" in offsets:
            start = _safe_float(offsets.get("from", 0.0)) / 1000.0
            end = _safe_float(offsets.get("to", 0.0)) / 1000.0
        elif "from" in timestamps or "to" in timestamps:
            start = _parse_timestamp_text(timestamps.get("from", "0"))
            end = _parse_timestamp_text(timestamps.get("to", "0"))

        if start is None:
            start = 0.0
        if end is None:
            end = start

        normalized.append(
            {
                "start": float(max(0.0, start)),
                "end": float(max(float(start), end)),
                "text": text,
            }
        )
    return normalized


def _gpu_device_visible() -> bool:
    return Path("/dev/dri").exists()


def _backend_attempts() -> list[str]:
    if BACKEND_PREFERENCE == "cpu":
        return ["cpu"]
    if BACKEND_PREFERENCE == "gpu":
        return ["gpu", "cpu"] if CPU_FALLBACK_ENABLED else ["gpu"]

    if _gpu_device_visible():
        return ["gpu", "cpu"] if CPU_FALLBACK_ENABLED else ["gpu"]
    return ["cpu"]


def _run_whisper(temp_audio_path: str, backend: str) -> tuple[subprocess.CompletedProcess[str], str]:
    env = os.environ.copy()
    cmd = [
        WHISPER_CPP_PATH,
        "-m",
        MODEL_PATH,
        "-f",
        temp_audio_path,
        "-oj",
        "-of",
        temp_audio_path,
    ]

    # The Vulkan build can still run on CPU; explicitly disable Vulkan when retrying
    # the fallback path so a bad GPU stack does not repeatedly poison requests.
    if backend == "cpu":
        env["GGML_VK_DISABLE"] = "1"
    else:
        env.pop("GGML_VK_DISABLE", None)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    return result, backend

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "backend_preference": BACKEND_PREFERENCE,
        "cpu_fallback_enabled": CPU_FALLBACK_ENABLED,
        "gpu_device_visible": _gpu_device_visible(),
        "runtime": "whisper.cpp-vulkan-image",
    }

@app.post("/transcribe")
async def transcribe(file: UploadFile):
    """
    Transcribe audio using whisper.cpp with Vulkan GPU acceleration
    """
    audio = await file.read()
    if not audio:
        return {"text": "", "language": ""}

    # Write audio to temp file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
        temp_audio.write(audio)
        temp_audio_path = temp_audio.name

    # Create temp output file for JSON
    temp_json_path = temp_audio_path + ".json"

    try:
        result = None
        active_backend = "unknown"
        last_error = ""
        for backend in _backend_attempts():
            result, active_backend = _run_whisper(temp_audio_path, backend)
            if result.returncode == 0:
                break
            last_error = result.stderr or result.stdout or f"whisper.cpp {backend} attempt failed"
            print(f"whisper.cpp {backend} error: {last_error}")
        
        if result is None or result.returncode != 0:
            return {
                "text": "",
                "language": "",
                "error": last_error or "transcription failed",
                "backend": active_backend,
            }
        
        # Read JSON output
        if Path(temp_json_path).exists():
            with open(temp_json_path, 'r') as f:
                output = json.load(f)
                
            # Join ALL transcription segments (each ~30s chunk is a separate entry)
            segments = _normalize_segments(output)
            text = " ".join(
                seg.get("text", "").strip()
                for seg in segments
                if seg.get("text", "").strip()
            )
            
            return {
                "text": text,
                "segments": segments,
                "language": "en",  # whisper.cpp doesn't auto-detect in this mode
                "backend": active_backend,
            }
        else:
            # Fallback: parse stderr for text output
            text = result.stdout.strip() if result.stdout else ""
            return {"text": text, "segments": [], "language": "en", "backend": active_backend}
            
    except subprocess.TimeoutExpired:
        return {"text": "", "segments": [], "language": "", "error": "Transcription timeout"}
    except Exception as e:
        print(f"Transcription error: {e}")
        return {"text": "", "segments": [], "language": "", "error": str(e)}
    finally:
        # Cleanup temp files
        try:
            Path(temp_audio_path).unlink(missing_ok=True)
            Path(temp_json_path).unlink(missing_ok=True)
        except:
            pass
