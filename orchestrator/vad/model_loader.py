from pathlib import Path
from urllib.request import urlretrieve

from orchestrator.config import VoiceConfig


def ensure_silero_model(config: VoiceConfig, logger) -> str | None:
    if config.silero_model_path:
        return config.silero_model_path
    if not config.silero_auto_download:
        return None

    min_bytes = 1_000_000

    root_dir = Path(__file__).resolve().parents[2]
    cache_dir = root_dir / config.silero_model_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / "silero_vad.onnx"

    if model_path.exists():
        try:
            if model_path.stat().st_size >= min_bytes:
                return str(model_path)
            logger.warning("Silero model too small (%d bytes); re-downloading", model_path.stat().st_size)
            model_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        logger.info("Downloading Silero VAD model to %s", model_path)
        urlretrieve(config.silero_model_url, model_path)
        try:
            size = model_path.stat().st_size
        except OSError:
            size = 0
        if size < min_bytes:
            logger.warning("Downloaded Silero model size %d bytes is invalid; deleting", size)
            model_path.unlink(missing_ok=True)
            return None
        return str(model_path)
    except Exception as exc:  # pragma: no cover
        logger.warning("Silero model download failed: %s", exc)
        return None
