import os
import tempfile
import sys
import signal

from fastapi import FastAPI, UploadFile

app = FastAPI()

# Defer torch/whisper imports to avoid GPU initialization on startup
MODEL = None
device = None


def _normalize_segments(raw_segments):
    normalized = []
    for row in raw_segments or []:
        text = str(row.get("text", "") or "").strip()
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", start) or start)
        normalized.append(
            {
                "start": max(0.0, start),
                "end": max(start, end),
                "text": text,
            }
        )
    return normalized

def load_model_safely():
    """Load whisper model with GPU fallback"""
    global MODEL, device
    import torch
    import whisper
    
    MODEL_NAME = os.getenv("WHISPER_MODEL_NAME", "base")
    WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
    
    # Start with CPU to avoid GPU segfaults
    device = "cpu"
    try:
        print(f"Loading Whisper model {MODEL_NAME} on CPU (GPU fallback available)...")
        MODEL = whisper.load_model(MODEL_NAME, device="cpu")
        print(f"✓ Whisper initialized with model={MODEL_NAME}, device=cpu")
        
        # Try to switch to GPU if requested and available
        if WHISPER_DEVICE == "cuda":
            try:
                print(f"Attempting GPU inference (if available)...")
                MODEL = whisper.load_model(MODEL_NAME, device="cuda")
                device = "cuda"
                print(f"✓ Switched to GPU for inference")
            except Exception as gpu_err:
                print(f"GPU not available, staying on CPU: {gpu_err}")
    except Exception as e:
        print(f"✗ Failed to load model: {e}")
        raise

@app.on_event("startup")
async def startup_event():
    """Load model on startup, using signal alarm to force timeout"""
    try:
        # Set a 60 second timeout for model loading
        def timeout_handler(signum, frame):
            print("Model loading timed out after 60 seconds, exiting...")
            sys.exit(1)
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(60)
        
        load_model_safely()
        
        signal.alarm(0)  # Cancel alarm
    except Exception as e:
        print(f"Startup error: {e}")

@app.post("/transcribe")
async def transcribe(file: UploadFile):
    if MODEL is None:
        return {"error": "Model not initialized", "text": "", "language": ""}
    
    audio = await file.read()
    if not audio:
        return {"text": "", "language": ""}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp_audio:
        temp_audio.write(audio)
        temp_audio.flush()
        result = MODEL.transcribe(temp_audio.name)

    segments = _normalize_segments(result.get("segments"))

    return {
        "text": (result.get("text") or "").strip(),
        "segments": segments,
        "language": result.get("language", ""),
    }
