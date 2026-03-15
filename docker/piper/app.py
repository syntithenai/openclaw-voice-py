import io
import os
import wave
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from piper import PiperVoice

app = FastAPI()

VOICE_ID = os.getenv("PIPER_VOICE_ID", "en_US-amy-medium")
VOICE_PATH = Path("/root/.local/share/piper") / f"{VOICE_ID}.onnx"

if VOICE_PATH.exists():
    VOICE = PiperVoice.load(str(VOICE_PATH))
else:
    VOICE = PiperVoice.load(VOICE_ID)


@app.get("/voices")
def list_voices():
    voices = []
    models_dir = Path("/root/.local/share/piper")
    if models_dir.exists():
        for model_file in sorted(models_dir.glob("*.onnx")):
            voices.append(model_file.stem)
    return {
        "default": VOICE_ID,
        "voices": voices,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "piper-tts",
        "voice_id": VOICE_ID,
        "voice_loaded": VOICE is not None,
        "voice_path": str(VOICE_PATH),
    }


@app.post("/synthesize")
def synthesize(payload: dict):
    text = payload.get("text", "")
    voice = payload.get("voice", VOICE_ID)
    length_scale = payload.get("length_scale", 1.0)

    current_voice = VOICE
    if voice != VOICE_ID:
        try:
            alt_voice_path = Path("/root/.local/share/piper") / f"{voice}.onnx"
            if alt_voice_path.exists():
                current_voice = PiperVoice.load(str(alt_voice_path))
            else:
                current_voice = PiperVoice.load(voice)
        except Exception:
            current_voice = VOICE

    if not text:
        return Response(content=b"", media_type="audio/wav")

    wav_result = current_voice.synthesize(text)
    if isinstance(wav_result, (bytes, bytearray)):
        wav_bytes = bytes(wav_result)
    else:
        chunks = []
        sample_rate = 22050
        sample_width = 2
        sample_channels = 1
        for chunk in wav_result:
            if hasattr(chunk, "audio_int16_bytes"):
                sample_rate = getattr(chunk, "sample_rate", sample_rate)
                sample_width = getattr(chunk, "sample_width", sample_width)
                sample_channels = getattr(chunk, "sample_channels", sample_channels)
                chunks.append(chunk.audio_int16_bytes)
            elif isinstance(chunk, (bytes, bytearray)):
                chunks.append(bytes(chunk))
        pcm_bytes = b"".join(chunks)
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(sample_channels)
                wav_file.setsampwidth(sample_width)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_bytes)
            wav_bytes = buffer.getvalue()

    response = Response(content=wav_bytes, media_type="audio/wav")
    response.headers["X-Piper-Provider"] = "piper-tts"
    return response
