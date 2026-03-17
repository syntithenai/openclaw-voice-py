# Pyannote Docker Service Plan (Recorder Diarization)

## Objective
Run speaker diarization through a long-running `pyannote` web service in Docker, mirroring the existing Whisper/Piper microservice pattern, and use that service from the recorder skill.

## Scope
- Add a dedicated FastAPI service under `docker/pyannote/`
- Add compose integration (`pyannote:10002`)
- Add recorder config for service URL
- Route recorder diarization to service-first execution
- Keep local pyannote fallback path as backup for compatibility

## Service Design
### Endpoint contract
- `GET /health`
  - Returns readiness, backend mode, model id, token presence, device details.
- `POST /diarize`
  - Multipart file upload (`audio.wav`)
  - Optional form fields: `model_id`
  - Returns JSON:
    ```json
    {
      "segments": [
        {"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00"}
      ],
      "model": "pyannote/speaker-diarization-3.1",
      "backend": "gpu|cpu"
    }
    ```

### Runtime behavior
- Load pipeline lazily and cache in-memory.
- Default to GPU when available (`auto`), otherwise CPU.
- Respect fallback policy when GPU init fails.
- Use HF token from env (`PYANNOTE_AUTH_TOKEN`) for gated model access.

## Configuration additions
### Recorder/orchestrator
- `RECORDER_PYANNOTE_URL` (default `http://localhost:10002`)
- Existing flags remain:
  - `RECORDER_PYANNOTE_ENABLED`
  - `RECORDER_PYANNOTE_MODEL`
  - `RECORDER_PYANNOTE_AUTH_TOKEN`

### Service container
- `PYANNOTE_MODEL_ID` (default `pyannote/speaker-diarization-3.1`)
- `PYANNOTE_AUTH_TOKEN`
- `PYANNOTE_BACKEND_PREFERENCE` (`auto|gpu|cpu`)
- `PYANNOTE_CPU_FALLBACK` (`true|false`)

## Compose integration
- Add `pyannote` service in `docker-compose.yml`.
- Expose `10002:10002`.
- Mount model cache volume (`./docker/pyannote-models:/models`).
- Add orchestrator env wiring to internal endpoint:
  - `PYANNOTE_URL=http://pyannote:10002`

## Recorder integration
1. Add `PyannoteClient` HTTP client in orchestrator.
2. Instantiate client in `main.py` when recorder + pyannote enabled.
3. Recorder `_run_pyannote` order:
   - service call first (if client configured),
   - local pyannote fallback if service unavailable.
4. Preserve current transcript output format and diarization note handling.

## Validation checklist
- Static: `py_compile` for changed modules.
- Unit/regression: existing quick-answer and navigation tests still pass.
- Runtime smoke:
  1. start stack with `pyannote` service,
  2. start recording/stop recording,
  3. verify `.txt` contains speaker timeline without token-missing note.

## Rollback
- Set `RECORDER_PYANNOTE_ENABLED=false` to disable diarization.
- Or unset `RECORDER_PYANNOTE_URL` to force local-only fallback path.
