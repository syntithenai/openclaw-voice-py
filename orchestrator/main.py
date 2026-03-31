import warnings

warnings.filterwarnings(
    "ignore",
    message="invalid escape sequence.*",
    category=SyntaxWarning,
)

import os
import asyncio
import shutil
import subprocess
import sys
import io
import json
import logging
import math
import platform
import re
import threading
import time
import unicodedata
import wave
from collections import deque
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit
from urllib.request import urlretrieve

from orchestrator.config import VoiceConfig
from orchestrator.state import VoiceState, WakeState
from orchestrator.audio.capture import AudioCapture
from orchestrator.audio.duplex import DuplexAudioIO
from orchestrator.audio.buffer import RingBuffer
from orchestrator.audio.volume_adjuster import CutInTracker, MicVolumeAdjuster
from orchestrator.vad.silero import SileroVAD
from orchestrator.vad.webrtc_vad import WebRTCVAD
from orchestrator.wakeword.openwakeword import OpenWakeWordDetector
from orchestrator.wakeword.precise import MycoftPreciseDetector
from orchestrator.wakeword.picovoice import PicovoiceDetector
from orchestrator.stt.whisper_client import WhisperClient
from orchestrator.stt.pyannote_client import PyannoteClient
from orchestrator.emotion.sensevoice import SenseVoice
from orchestrator.gateway import build_gateway
from orchestrator.gateway.message_extract import (
    extract_text_from_gateway_message,
    strip_gateway_control_markers,
)
from orchestrator.tts.piper_client import PiperClient
from orchestrator.tts_policy import tts_start_gate_block_reason
from orchestrator.tts.text_progress import estimate_spoken_prefix, strip_spoken_prefix
from orchestrator.audio.playback import AudioPlayback
from orchestrator.audio.webrtc_aec import WebRTCAEC
from orchestrator.audio.pcm_utils import pcm_to_wav_bytes, wav_bytes_to_pcm, wav_bytes_to_pcm_with_rate
from orchestrator.audio.resample import resample_pcm
from orchestrator.audio.device_selection import (
    _resolve_device_index,
    _rank_device_priority,
    _auto_select_audio_device,
    _auto_select_physical_input_device,
    _pick_working_playback_rate,
    _describe_device,
)
from orchestrator.audio.sounds import (
    generate_click_sound,
    generate_swoosh_sound,
    generate_cluck_sound,
    generate_sigh_sound,
    generate_knock_sound,
    generate_exhale_sound,
)
from orchestrator.metrics import AECStatus, WakeWordResult
from orchestrator.runtime.config_validation import validate_runtime_config
from orchestrator.platform.hardware import is_raspberry_pi
from orchestrator.music.parser import MusicFastPathParser
from orchestrator.tools.recorder import RecorderTool, compute_hotword_stop_trim_seconds
from orchestrator.vad.model_loader import ensure_silero_model
import numpy as np


@dataclass
class TTSQueueItem:
    text: str
    request_id: int
    kind: str  # "reply" | "notification"
    created_ts: float
    allow_when_ui_tts_muted: bool = False


@dataclass(frozen=True)
class GhostDecision:
    accepted: bool
    reason_codes: tuple[str, ...]
    score: int
    matched_priority_rule: str


ACK_TOKENS = {
    "yes",
    "no",
    "okay",
    "ok",
    "sure",
    "thanks",
    "thank you",
    "right",
    "yep",
    "nope",
    "correct",
}

GREETING_TOKENS = {
    "hello",
    "hi",
    "hey",
}

GHOST_ARTIFACT_TOKENS = {
    "hello",
    "hi",
    "thanks",
    "thank you",
    "thanks for watching",
    "hmm",
    "sigh",
    "sighs",
    "you re welcome",
    "you're welcome",
    "youre welcome",
    "subtitles by the amara org community",
    "subtitles by amara org community",
    "subtitles by the amara org",
    "i'm sorry",
    "im sorry",
    "i don't know",
    "i dont know",
    # TV / broadcast background noise phrases
    "we'll be right back",
    "we will be right back",
    "i'll be right back",
    "i will be right back",
    "be right back",
    "brb",
    "we're back",
    "we are back",
    "and we're back",
    "and we are back",
    "stay tuned",
    "don't go anywhere",
    "don't touch that dial",
    "after the break",
    "after these messages",
    "right after this",
    "right back after this",
}

GHOST_ARTIFACT_PATTERNS = (
    re.compile(r"\bi(?:'m|\s+am)?\s+going\s+to\s+go\s+ahead\s+and\s+stop\s+(?:the\s+)?record(?:ing)?\b"),
    re.compile(r"\b(?:we(?:'ll|'re| will| are)?|i(?:'ll| will)?)\s+(?:be\s+)?right\s+back\b"),
    re.compile(r"\bwe(?:'re| are)\s+(?:back|coming\s+back)\b"),
    re.compile(r"\bstay\s+tuned\b"),
)

GHOST_SHORT_COMMAND_PARSER = MusicFastPathParser()
GHOST_SHORT_COMMAND_ALLOW_CMDS = {
    "next_track",
    "previous_track",
    "play",
    "stop",
    "volume_up",
    "volume_down",
    "set_volume",
    "get_current_track",
    "get_status",
}


def canonicalize_transcript_for_match(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    lowered = lowered.replace("’", "'")
    lowered = re.sub(r"[^a-z0-9\s']+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def assistant_turn_is_question(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if s.endswith("?"):
        return True
    return bool(
        re.search(
            r"\b(which|what|when|where|who|did you mean|do you mean|which one do you mean)\b",
            s,
        )
    )


def assistant_turn_expects_short_reply(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if assistant_turn_is_question(s):
        return True
    return bool(
        re.search(
            r"\b(do you want|would you like|should i|can you confirm|which one|choose|pick one|confirm)\b",
            s,
        )
    )


def is_ack_token(canonical: str) -> bool:
    return canonical in ACK_TOKENS


def is_greeting_token(canonical: str) -> bool:
    return canonical in GREETING_TOKENS


def is_startup_welcome_pattern(text: str) -> bool:
    """Detect common startup/cold-start welcome messages to suppress them.
    
    Matches patterns like:
    - "Hey. I just came online. How can I help you today?"
    - "Hello. I'm ready to assist."
    - "I just came online."
    - "How can I help you today?"
    - etc.
    """
    canonical = canonicalize_transcript_for_match(text)
    if not canonical:
        return False
    
    # Startup greeting patterns
    startup_patterns = (
        re.compile(r"\b(just\s+)?came\s+online\b"),
        re.compile(r"i\s+just\s+came\s+online\b"),
        re.compile(r"\bhey\s*\.?\s+(i\s+just\s+came\s+online|how\s+can\s+i\s+help|what\s+can\s+i\s+do)"),
        re.compile(r"\b(hello|hey|hi)\s*\.?\s+(i|how|what|welcome)"),
        re.compile(r"^(hello|hey|hi)\s+(i|how|what|can)\b"),
        re.compile(r"\b(how|what)\s+can\s+(i|we)\s+(help|do|assist)\s+(you|for|today)"),
        re.compile(r"\b(ready|willing|happy|glad)\s+(to\s+)?(help|assist|listen)"),
        re.compile(r"^\s*(hello|hey|hi)\s*\.?\s*$"),  # Just hello/hey/hi
    )
    
    for pattern in startup_patterns:
        if pattern.search(canonical):
            return True
    return False


def has_supported_short_command(canonical: str, token_count: int) -> bool:
    if not canonical or token_count <= 0 or token_count > 3:
        return False
    try:
        parsed = GHOST_SHORT_COMMAND_PARSER.parse(canonical)
        if not parsed:
            return False
        command, _params = parsed
        return command in GHOST_SHORT_COMMAND_ALLOW_CMDS
    except Exception:
        return False


def score_self_echo_similarity(transcript: str, recent_tts_texts: Sequence[str]) -> float:
    canonical = canonicalize_transcript_for_match(transcript)
    if not canonical:
        return 0.0

    tx_words = canonical.split()
    tx_set = set(tx_words)
    best_score = 0.0
    for candidate_raw in recent_tts_texts:
        candidate = canonicalize_transcript_for_match(candidate_raw)
        if not candidate:
            continue
        if canonical == candidate:
            return 1.0
        if len(canonical) >= 8 and (canonical in candidate or candidate in canonical):
            best_score = max(best_score, 0.92)

        cand_words = candidate.split()
        cand_set = set(cand_words)
        if not tx_set or not cand_set:
            continue
        overlap = len(tx_set & cand_set) / float(max(len(tx_set), 1))
        candidate_overlap = len(tx_set & cand_set) / float(max(len(cand_set), 1))
        combined = (overlap * 0.65) + (candidate_overlap * 0.35)
        best_score = max(best_score, combined)
    return max(0.0, min(1.0, best_score))


def decide_ghost_transcript(ctx: dict[str, Any]) -> GhostDecision:
    transcript = str(ctx.get("transcript_text") or "").strip()
    canonical = str(ctx.get("canonical_transcript") or "")
    token_count = int(ctx.get("token_count") or 0)
    has_alnum = bool(re.search(r"[a-zA-Z0-9]", transcript))
    is_single_word = bool(ctx.get("is_single_word"))
    is_short_transcript = bool(ctx.get("is_short_transcript"))
    short_command_supported = has_supported_short_command(canonical, token_count)

    if not transcript:
        return GhostDecision(False, ("empty_transcript",), -9, "hard_reject_empty")
    if not has_alnum:
        return GhostDecision(False, ("punctuation_only",), -9, "hard_reject_punctuation")

    recorder_active = bool(ctx.get("recorder_active"))
    if not recorder_active and any(pattern.search(canonical) for pattern in GHOST_ARTIFACT_PATTERNS):
        return GhostDecision(False, ("ghost_recorder_stop_phrase",), -7, "hard_reject_recorder_stop_artifact")

    similarity = float(ctx.get("self_echo_similarity") or 0.0)
    similarity_threshold = float(ctx.get("self_echo_similarity_threshold") or 0.75)
    tts_playing = bool(ctx.get("tts_playing"))
    ms_since_tts_end = float(ctx.get("ms_since_tts_end") or 999999.0)
    playback_tail_ms = float(ctx.get("playback_tail_ms") or 1200)

    # High similarity should only hard-reject while playback is active or in the
    # immediate post-playback tail. Otherwise repeated user commands much later
    # (e.g. another alarm request) can be falsely suppressed as "self echo".
    if similarity >= similarity_threshold and (tts_playing or ms_since_tts_end <= playback_tail_ms):
        return GhostDecision(False, ("self_echo_high_similarity",), -8, "hard_reject_self_echo")

    if tts_playing and canonical in {"you re welcome", "you're welcome", "youre welcome", "thanks", "thank you"}:
        return GhostDecision(False, ("active_playback_echo_phrase",), -8, "hard_reject_playback_echo")

    last_assistant_was_question = bool(ctx.get("last_assistant_was_question"))
    last_assistant_expects_short_reply = bool(ctx.get("last_assistant_expects_short_reply"))
    last_user_went_upstream = bool(ctx.get("last_user_went_upstream"))
    last_upstream_response_was_question = bool(ctx.get("last_upstream_response_was_question"))
    last_upstream_response_requested_confirmation = bool(ctx.get("last_upstream_response_requested_confirmation"))
    upstream_context_is_fresh = bool(ctx.get("upstream_context_is_fresh"))

    allow_codes: list[str] = []
    if token_count <= 3 and last_assistant_was_question:
        allow_codes.append("allow_assistant_question")
    if token_count <= 3 and last_user_went_upstream and last_upstream_response_was_question and upstream_context_is_fresh:
        allow_codes.append("allow_upstream_question")
    if token_count <= 4 and last_upstream_response_requested_confirmation and upstream_context_is_fresh:
        allow_codes.append("allow_upstream_clarification")
    if is_ack_token(canonical) and last_assistant_expects_short_reply:
        allow_codes.append("allow_direct_confirmation")

    strong_allow = bool(allow_codes)

    cut_in_active = bool(ctx.get("cut_in_active"))
    ms_from_cut_in_start = float(ctx.get("ms_from_cut_in_start") or 999999.0)
    cutin_early_ms = float(ctx.get("cutin_early_ms") or 500)
    require_question_for_acks = bool(ctx.get("require_question_for_acks"))
    single_word_enabled = bool(ctx.get("single_word_enabled"))
    has_inflight_user_request = bool(ctx.get("has_inflight_user_request"))

    if canonical in GHOST_ARTIFACT_TOKENS and not strong_allow:
        return GhostDecision(False, ("ghost_artifact_phrase",), -4, "strong_suppress_artifact")

    if single_word_enabled and is_single_word and cut_in_active and ms_from_cut_in_start <= cutin_early_ms and not strong_allow:
        return GhostDecision(False, ("cutin_early_single_word",), -3, "strong_suppress_cutin_blip")

    if single_word_enabled and is_single_word and ms_since_tts_end <= playback_tail_ms and similarity >= 0.45 and not strong_allow:
        return GhostDecision(False, ("playback_tail_blip",), -3, "strong_suppress_playback_tail")

    if require_question_for_acks and is_ack_token(canonical):
        has_question_context = last_assistant_was_question or (last_user_went_upstream and last_upstream_response_was_question and upstream_context_is_fresh)
        if not has_question_context and not strong_allow:
            return GhostDecision(False, ("ack_without_question_context",), -3, "strong_suppress_ack_no_question")

    if is_greeting_token(canonical) and not strong_allow and not bool(ctx.get("has_fresh_prompt_context")):
        return GhostDecision(False, ("standalone_greeting",), -3, "strong_suppress_greeting")

    score = 0
    if last_upstream_response_was_question and upstream_context_is_fresh:
        score += 4
    if last_assistant_was_question:
        score += 3
    if last_upstream_response_requested_confirmation and upstream_context_is_fresh:
        score += 3
    if last_user_went_upstream and upstream_context_is_fresh:
        score += 2
    if token_count >= 2 and token_count <= 3:
        score += 2
    if token_count >= 4:
        score += 1

    if similarity >= similarity_threshold:
        score -= 5
    if canonical in GHOST_ARTIFACT_TOKENS:
        score -= 4
    if is_single_word and is_ack_token(canonical) and not (last_assistant_was_question or last_upstream_response_was_question):
        score -= 3
    if single_word_enabled and is_single_word and cut_in_active and ms_from_cut_in_start <= cutin_early_ms:
        score -= 3
    if is_single_word and ms_since_tts_end <= playback_tail_ms and similarity >= 0.35:
        score -= 2

    if strong_allow and score <= 0:
        return GhostDecision(True, tuple(allow_codes), score, "hard_allow_context")
    if score >= 1:
        return GhostDecision(True, tuple(allow_codes) if allow_codes else ("weighted_accept",), score, "weighted_accept")

    if is_short_transcript and has_inflight_user_request and not is_ack_token(canonical) and not is_greeting_token(canonical):
        return GhostDecision(True, ("allow_inflight_short_continuation",), score, "continuation_allow_inflight")

    # If a short transcript already matches the deterministic music fast-path,
    # treat it as a supported command instead of dropping it as unsupported noise.
    if is_short_transcript and short_command_supported:
        return GhostDecision(True, ("allow_supported_short_command",), score, "hard_allow_supported_short_command")

    if token_count >= 4:
        return GhostDecision(True, ("default_accept_normal_length",), score, "default_accept")
    if is_short_transcript:
        return GhostDecision(False, ("default_suppress_short_without_support",), score, "default_suppress_short")
    return GhostDecision(True, ("default_accept",), score, "default_accept")


# Custom logging formatter with selective color highlighting for transcriptions
class ColoredFormatter(logging.Formatter):
    """Formatter that highlights transcribed speech and TTS responses in green."""
    
    GREEN = '\033[92m'
    RESET = '\033[0m'
    
    def format(self, record):
        msg = super().format(record)
        
        # Highlight transcribed speech: ' text' in STT Complete lines
        if 'STT: Complete in' in msg:
            # Extract text between quotes after "Complete in X ms: "
            import re
            match = re.search(r"Complete in \d+ms: ('.*?')", msg)
            if match:
                quoted_text = match.group(1)
                highlighted = self.GREEN + quoted_text + self.RESET
                msg = msg.replace(quoted_text, highlighted)
        
        # Highlight TTS response text in queue/synth lines
        if 'TTS QUEUE: Enqueuing response:' in msg or 'TTS SYNTH: Generating speech for:' in msg:
            import re
            # Extract text between quotes
            match = re.search(r": ('.*?)$", msg)
            if match:
                quoted_text = match.group(1)
                highlighted = self.GREEN + quoted_text + self.RESET
                msg = msg.replace(quoted_text, highlighted)
        
        return msg


AUDIO_LOG_LEVEL = 25
logging.addLevelName(AUDIO_LOG_LEVEL, "AUDIO")


def _resolve_runtime_log_level() -> int:
    raw = str(os.environ.get("OPENCLAW_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "WARNING").strip().upper()
    if raw == "AUDIO":
        return AUDIO_LOG_LEVEL
    return getattr(logging, raw, logging.WARNING)


logging.basicConfig(
    level=_resolve_runtime_log_level(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,  # Force reconfiguration
)
logger = logging.getLogger("orchestrator")

# Apply colored formatter
for handler in logging.root.handlers:
    handler.setFormatter(ColoredFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

# Suppress verbose logging from FunASR and other libraries
logging.getLogger("funasr").setLevel(logging.ERROR)
logging.getLogger("modelscope").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# Ensure immediate flushing
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


async def run_orchestrator() -> None:
    config = VoiceConfig()
    source_root = Path(__file__).resolve().parent.parent
    workspace_root = Path(config.openclaw_workspace_dir).expanduser()
    if not workspace_root.is_absolute():
        workspace_root = (source_root / workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    os.environ["OPENCLAW_WORKSPACE_DIR"] = str(workspace_root)
    os.environ.setdefault("OPENCLAW_WORKSPACE", str(workspace_root))
    
    # Smart defaults: enable ring buffer clearing on ARM systems to prevent ghost transcripts
    if is_raspberry_pi() and config.wake_clear_ring_buffer is False:
        config.wake_clear_ring_buffer = True
        logger.info("Auto-enabling wake_clear_ring_buffer on ARM architecture")
    
    # Print immediately so user sees something
    print("\n" + "="*51, flush=True)
    print("  OpenClaw Voice Orchestrator - Initializing", flush=True)
    print("="*51 + "\n", flush=True)
    
    logger.info("Starting Python voice orchestrator (scaffold)")
    
    # Validate runtime configuration before proceeding with initialization
    print("→ Validating runtime configuration...", flush=True)
    validate_runtime_config(config)

    frame_samples = int(config.audio_sample_rate * (config.audio_frame_ms / 1000))
    logger.info("═══════════════════════════════════════════════════")
    logger.info("  OpenClaw Voice Orchestrator - Initializing")
    logger.info("═══════════════════════════════════════════════════")
    logger.info("Audio config: device=%s, sample_rate=%d Hz, frame_ms=%d", 
                config.audio_capture_device, config.audio_sample_rate, config.audio_frame_ms)
    logger.info("VAD config: type=%s, confidence=%.2f, min_silence=%d ms", 
                config.vad_type, config.vad_confidence, config.vad_min_silence_ms)
    
    ring_buffer = RingBuffer(max_frames=int(config.pre_roll_ms / config.audio_frame_ms))

    if config.audio_backend == "portaudio-duplex":
        duplex = DuplexAudioIO(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            input_device=config.audio_capture_device,
            output_device=config.audio_playback_device,
            input_gain=config.audio_input_gain,
        )
        capture = duplex
        playback = duplex
        logger.info("Audio duplex initialized (input=%s, output=%s, gain=%.1fx)", config.audio_capture_device, config.audio_playback_device, config.audio_input_gain)
    else:
        # Runtime audio selection policy:
        # 1) Try configured .env devices first
        # 2) On fatal init/start errors, auto-select based on available hardware
        # 3) Do NOT modify .env (next restart retries configured values first)
        selected_capture_device: int | str = config.audio_capture_device
        selected_playback_device: int | str = config.audio_playback_device
        selected_playback_rate = config.audio_playback_sample_rate if config.audio_playback_sample_rate > 0 else config.audio_sample_rate

        try:
            cap_idx = _resolve_device_index(config.audio_capture_device, want_input=True)
            pb_idx = _resolve_device_index(config.audio_playback_device, want_input=False)

            # Validate configured capture device by opening check settings.
            import sounddevice as sd

            cap_ok = False
            cap_err = None
            for ch in (1, 2):
                try:
                    sd.check_input_settings(device=cap_idx, samplerate=config.audio_sample_rate, channels=ch)
                    cap_ok = True
                    break
                except Exception as exc:
                    cap_err = exc

            if not cap_ok:
                raise RuntimeError(f"Configured capture device failed validation ({config.audio_capture_device}): {cap_err}")

            # Validate configured playback device/rate.
            pb_info = sd.query_devices(pb_idx, "output")
            if pb_info.get("max_output_channels", 0) == 0:
                raise RuntimeError(f"Configured playback device has no output channels ({config.audio_playback_device})")
            selected_playback_rate = _pick_working_playback_rate(pb_idx, selected_playback_rate)
            sd.check_output_settings(device=pb_idx, samplerate=selected_playback_rate, channels=1)

            # Keep configured values for this run.
            selected_capture_device = config.audio_capture_device
            selected_playback_device = config.audio_playback_device
            logger.info(
                "Audio device validation passed using configured devices (capture=%s, playback=%s, rate=%s)",
                _describe_device(cap_idx),
                _describe_device(pb_idx),
                selected_playback_rate,
            )
        except Exception as audio_exc:
            logger.error("Configured audio initialization failed: %s", audio_exc)
            logger.warning("Attempting automatic audio device selection (without changing .env)")

            auto_cap_idx = _auto_select_audio_device(want_input=True)
            auto_pb_idx = _auto_select_audio_device(want_input=False)

            if auto_cap_idx is None or auto_pb_idx is None:
                raise RuntimeError(
                    "No compatible audio devices found for automatic fallback; please reconnect audio hardware"
                ) from audio_exc

            selected_capture_device = auto_cap_idx
            selected_playback_device = auto_pb_idx
            selected_playback_rate = _pick_working_playback_rate(auto_pb_idx, selected_playback_rate)

            logger.warning(
                "Using auto-selected audio devices for this run: capture=%s playback=%s rate=%s Hz",
                _describe_device(auto_cap_idx),
                _describe_device(auto_pb_idx),
                selected_playback_rate,
            )
            logger.warning(".env remains unchanged; next restart will retry configured devices first")

        capture = AudioCapture(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            device=selected_capture_device,
            input_gain=config.audio_input_gain,
        )
        logger.info("Audio capture initialized on device: %s (gain=%.1fx)", selected_capture_device, config.audio_input_gain)

    # VAD initialization
    print("→ Loading VAD model...", flush=True)
    logger.info("→ Loading VAD model (%s)...", config.vad_type)
    vad_start = time.monotonic()
    if config.vad_type.lower() == "webrtc":
        vad = WebRTCVAD(sample_rate=config.audio_sample_rate, frame_ms=config.audio_frame_ms)
    else:
        silero_path = ensure_silero_model(config, logger)
        vad = SileroVAD(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            model_path=silero_path or None,
        )
    vad_elapsed = int((time.monotonic() - vad_start) * 1000)
    logger.info("✓ VAD loaded in %dms", vad_elapsed)
    print(f"✓ VAD loaded in {vad_elapsed}ms", flush=True)

    cut_in_silero: SileroVAD | None = None
    if config.vad_cut_in_use_silero:
        if isinstance(vad, SileroVAD):
            cut_in_silero = vad
            logger.info("✓ Cut-in Silero VAD: using primary Silero model")
        else:
            logger.info("→ Loading Silero VAD for cut-in gate...")
            # Use ensure_silero_model to get path with v5 model URL from config
            silero_path = ensure_silero_model(config, logger)
            cut_in_silero = SileroVAD(
                sample_rate=config.audio_sample_rate,
                frame_samples=frame_samples,
                model_path=silero_path or None,
            )
            if cut_in_silero.loaded:
                logger.info("✓ Cut-in Silero VAD loaded")
            else:
                logger.warning("Cut-in Silero VAD failed to load; disabling Silero gate")
                cut_in_silero = None
                config.vad_cut_in_use_silero = False

    state = VoiceState.IDLE
    wake_state = WakeState.AWAKE if not config.wake_word_enabled else WakeState.ASLEEP
    last_activity_ts = time.monotonic()
    last_speech_ts: float | None = None
    chunk_start_ts: float | None = None
    chunk_frames: list[bytes] = []
    cut_in_triggered_ts: float | None = None
    active_transcriptions = 0
    tts_playing = False
    tts_base_gain = config.audio_output_gain
    tts_gain = tts_base_gain
    last_playback_frame: bytes | None = None
    last_tts_text = ""
    last_tts_ts = 0.0
    tts_dedupe_window_ms = 800
    cut_in_tts_hold_active = False
    cut_in_tts_hold_started_ts: float | None = None
    cut_in_tts_hold_request_id = 0
    current_request_id = 0  # Incremented on each user message
    current_tts_request_id = 0  # Tracks which request is currently playing
    tts_last_played_request_id = 0  # Request ID of the last successfully completed TTS item
    last_gateway_send_ts: float | None = None  # Monotonic time of the last transcript sent to gateway
    last_thinking_phrase_ts: float | None = None  # Monotonic time of the last thinking phrase spoken
    # Track if we're in the startup phase (no user messages sent yet)
    startup_phase_active = bool(config.new_session_suppress_welcome_message)
    # Legacy flag: suppress ALL gateway messages on new session (backward compat)
    suppress_gateway_messages_for_new_session = False
    gateway_collation_open = False
    gateway_collation_active_request_id = 0
    gateway_collation_last_frame_ts: float | None = None
    gateway_collation_close_task: asyncio.Task | None = None
    dropped_out_of_window_gateway_frames = 0
    last_user_text = ""
    last_user_accepted_ts: float | None = None
    last_user_went_upstream = False
    last_assistant_text = ""
    last_assistant_source = ""
    last_assistant_ts: float | None = None
    last_assistant_was_question = False
    last_assistant_expects_short_reply = False
    last_upstream_assistant_text = ""
    last_upstream_assistant_ts: float | None = None
    last_upstream_response_was_question = False
    last_upstream_response_requested_confirmation = False
    
    # Dynamic volume adjustment
    cut_in_tracker = CutInTracker(
        enabled=config.auto_adjust_output_volume_on_repeated_cutin,
        window_ms=config.auto_adjust_cutin_window_ms,
        count_threshold=config.auto_adjust_cutin_count_threshold,
        reduction_ratio=config.auto_adjust_output_volume_reduction_ratio,
        restoration_timeout_ms=config.auto_adjust_output_volume_restoration_timeout_ms,
    )
    mic_volume_adjuster = MicVolumeAdjuster(
        enabled=config.auto_adjust_mic_volume_enabled,
        target_rms=config.auto_adjust_mic_target_rms,
        adjustment_ratio=config.auto_adjust_mic_adjustment_ratio,
        exclude_devices=[d.strip() for d in config.auto_adjust_mic_exclude_devices.split(',') if d.strip()],
        gain_min=config.auto_adjust_mic_gain_min,
        gain_max=config.auto_adjust_mic_gain_max,
    )
    if config.auto_adjust_output_volume_on_repeated_cutin:
        logger.info(
            "🔊 Dynamic output volume adjustment ENABLED (cut-in tracker: %d in %dms → %.0f%% reduction)",
            config.auto_adjust_cutin_count_threshold,
            config.auto_adjust_cutin_window_ms,
            (1 - config.auto_adjust_output_volume_reduction_ratio) * 100,
        )
    if config.auto_adjust_mic_volume_enabled:
        logger.info(
            "🎤 Dynamic mic volume adjustment ENABLED (target RMS: %.4f, gain range: %.2f–%.2f)",
            config.auto_adjust_mic_target_rms,
            config.auto_adjust_mic_gain_min,
            config.auto_adjust_mic_gain_max,
        )
    
    ghost_suppressed_total = 0
    ghost_suppressed_short_no_question = 0
    ghost_suppressed_self_echo = 0
    ghost_accepted_short_after_question = 0
    ghost_accepted_short_after_upstream_question = 0
    warned_wake_resample = False
    warned_aec_stub = False
    wake_sleep_ts: float | None = None
    wake_sleep_cooldown_ms = max(0, config.wake_sleep_cooldown_ms)
    last_wake_detected_ts: float | None = None
    recorder_stop_hotword_armed_ts: float | None = None
    last_wake_conf_log_ts = 0.0
    last_timeout_progress_log_ts = 0.0  # Rate-limit for inactivity progress logs
    warned_missing_playerctl = False

    wake_detector = None
    active_wake_engine = None
    if config.wake_word_enabled:
        wake_start = time.monotonic()
        
        # Select wake word engine based on enabled flags
        if config.openwakeword_enabled:
            active_wake_engine = "openwakeword"
            logger.info("→ Loading Wake Word detector (OpenWakeWord: %s)...", config.openwakeword_wake_word)
            wake_detector = OpenWakeWordDetector(
                model_path=config.openwakeword_model_path,
                confidence=config.openwakeword_confidence,
            )
        elif config.precise_enabled:
            active_wake_engine = "precise"
            logger.info("→ Loading Wake Word detector (Precise: %s)...", config.precise_wake_word)
            wake_detector = MycoftPreciseDetector(
                model_path=config.precise_model_path,
                confidence=config.precise_confidence,
            )
        elif config.picovoice_enabled:
            active_wake_engine = "picovoice"
            logger.info("→ Loading Wake Word detector (Picovoice: %s)...", config.picovoice_wake_word)
            wake_detector = PicovoiceDetector(
                model_path=config.picovoice_wake_word,
                access_key=config.picovoice_key,
                confidence=config.picovoice_confidence,
            )
        
        wake_elapsed = int((time.monotonic() - wake_start) * 1000)
        if wake_detector:
            logger.info("✓ Wake Word detector loaded in %dms", wake_elapsed)
            # Warm up Precise detector to trigger TensorFlow loading (so it doesn't delay first real detection)
            # OpenWakeWord uses TFLite and doesn't need warm-up
            if active_wake_engine == "precise":
                logger.info("→ Warming up wake detector (loading TensorFlow models)...")
                warmup_start = time.monotonic()
                dummy_audio = np.zeros(2048, dtype=np.int16).tobytes()
                try:
                    wake_detector.detect(dummy_audio)
                    # Reset detector state after warm-up to clear the dummy audio from internal buffer
                    if hasattr(wake_detector, 'reset_state'):
                        wake_detector.reset_state()
                    warmup_elapsed = int((time.monotonic() - warmup_start) * 1000)
                    logger.info("✓ Wake detector ready (TensorFlow loaded in %dms)", warmup_elapsed)
                except Exception as e:
                    logger.warning("Wake detector warm-up failed: %s", e)
        else:
            logger.error("⚠ Wake word detector failed to initialize; staying ASLEEP to avoid always-on transcription")
            if config.openwakeword_enabled:
                logger.error("   Check OPENWAKEWORD_MODEL_PATH=%s", config.openwakeword_model_path)
            elif config.precise_enabled:
                logger.error("   Check PRECISE_MODEL_PATH=%s", config.precise_model_path)
            elif config.picovoice_enabled:
                logger.error("   Check PICOVOICE_KEY configuration")
            wake_state = WakeState.ASLEEP
            last_wake_detected_ts = None

    # STT client
    print("→ Initializing Whisper STT client...", flush=True)
    logger.info("→ Initializing Whisper STT client (%s)...", config.whisper_url)
    whisper_start = time.monotonic()
    whisper_client = WhisperClient(config.whisper_url)
    whisper_elapsed = int((time.monotonic() - whisper_start) * 1000)
    logger.info("✓ Whisper client ready in %dms", whisper_elapsed)
    print(f"✓ Whisper client ready in {whisper_elapsed}ms", flush=True)
    
    # Emotion model
    emotion_model_ref = config.sensevoice_model_path or config.emotion_model or None
    if config.emotion_enabled and emotion_model_ref:
        logger.info("→ Loading SenseVoice model (%s)... (this may take 30-60 seconds)", emotion_model_ref)
        print(f"\n→ Loading SenseVoice model ({emotion_model_ref})...", flush=True)
        print("  (Suppressing FunASR verbose output - please wait...)", flush=True)
        emotion_start = time.monotonic()
        # Suppress FunASR verbose output to both stdout and stderr
        with open(os.devnull, 'w') as devnull:
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            try:
                sys.stdout = devnull
                sys.stderr = devnull
                emotion = SenseVoice(model_path=emotion_model_ref)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
        emotion_elapsed = int((time.monotonic() - emotion_start) * 1000)
        logger.info("✓ SenseVoice loaded in %dms", emotion_elapsed)
        print(f"✓ SenseVoice loaded in {emotion_elapsed}ms\n", flush=True)
    else:
        emotion = None
    
    # Gateway
    print("→ Initializing gateway...", flush=True)
    logger.info("→ Initializing gateway (%s)...", config.gateway_provider)
    gateway_start = time.monotonic()
    gateway = build_gateway(config)
    gateway_elapsed = int((time.monotonic() - gateway_start) * 1000)
    logger.info("✓ Gateway ready in %dms", gateway_elapsed)
    print(f"✓ Gateway ready in {gateway_elapsed}ms", flush=True)
    
    # Native media backend runs in-process and does not require external daemon startup.
    
    # Use the session prefix directly as a stable session name rather than appending a
    # timestamp.  A stable name means all voice interactions accumulate in one persistent
    # session (e.g. agent:voice:main) so the web chat UI always shows the full history.
    session_id = config.gateway_session_prefix
    agent_id = config.gateway_agent_id or "assistant"
    
    # Tool System (timers/alarms)
    tool_router = None
    tool_monitor = None
    alert_gen = None
    timer_manager = None
    alarm_manager = None
    timers_feature_enabled = bool(config.tools_enabled and config.timers_enabled)
    if timers_feature_enabled:
        print("→ Initializing Tool System (timers/alarms)...", flush=True)
        logger.info("→ Initializing Tool System...")
        from orchestrator.tools.router import ToolRouter
        from orchestrator.tools.monitor import ToolMonitor
        from orchestrator.tools.state import StateManager
        from orchestrator.tools.timer import TimerManager
        from orchestrator.tools.alarm import AlarmManager
        from orchestrator.alerts import AlertGenerator
        
        # Ensure timers directory exists in configured workspace root
        timers_dir = workspace_root / config.tools_persist_dir
        timers_dir.mkdir(exist_ok=True)
        
        # Initialize alert generator
        alert_gen = AlertGenerator(sample_rate=config.audio_sample_rate)
        
        # Initialize state manager
        state_manager = StateManager(
            workspace_root=str(workspace_root),
            debounce_ms=config.tools_debounce_ms,
        )
        
        # Initialize managers
        timer_manager = TimerManager(state_manager=state_manager)
        alarm_manager = AlarmManager(state_manager=state_manager)
        
        # Initialize tool router
        tool_router = ToolRouter(
            timer_manager=timer_manager,
            alarm_manager=alarm_manager,
        )
        
        # Always restore persisted pending timers/alarms so active items survive restarts.
        # Legacy TOOLS_CLEAR_ON_STARTUP is intentionally ignored for safety.
        if config.tools_clear_on_startup:
            logger.warning(
                "Tool System: TOOLS_CLEAR_ON_STARTUP=true is ignored; "
                "preserving persisted pending timers/alarms on startup"
            )

        timers_restored, timers_expired_skipped = await timer_manager.load_from_disk()
        alarms_restored, alarms_expired_skipped = await alarm_manager.load_from_disk()
        logger.info(
            "Tool System: Startup restore summary timers(restored=%d skipped_expired=%d) "
            "alarms(restored=%d skipped_expired=%d)",
            timers_restored,
            timers_expired_skipped,
            alarms_restored,
            alarms_expired_skipped,
        )
        
        logger.info("✓ Tool System ready (persist_dir=%s)", timers_dir)
        print("✓ Tool System ready", flush=True)
    
    # Music Control System (native backend)
    music_router = None
    music_manager = None
    if config.music_enabled:
        print("→ Initializing Music Control System (native)...", flush=True)
        logger.info("→ Initializing Music Control System...")
        try:
            from orchestrator.music import NativeMusicClientPool, MusicManager, MusicRouter

            # Initialize native music client pools.
            music_pool = NativeMusicClientPool(
                pool_size=config.music_pool_size,
                timeout=config.music_command_timeout_s,
            )
            # Dedicated low-contention control pool for latency-sensitive commands
            # (e.g., clear/load/save/rm) so they do not queue behind heavy list calls.
            music_control_pool = NativeMusicClientPool(
                pool_size=1,
                timeout=config.music_command_timeout_s,
            )
            music_initialized = False
            music_attempts = 3
            for attempt in range(1, music_attempts + 1):
                try:
                    await music_pool.initialize()
                    await music_control_pool.initialize()
                    music_initialized = True
                    break
                except Exception as music_init_err:
                    if attempt < music_attempts:
                        logger.warning(
                            "Music backend initialization attempt %d/%d failed (%s). Retrying in 1s...",
                            attempt,
                            music_attempts,
                            music_init_err,
                        )
                        await asyncio.sleep(1)
                    else:
                        raise
            
            # Initialize music manager
            music_manager = MusicManager(
                music_pool,
                control_pool=music_control_pool,
                genre_queue_limit=config.music_genre_queue_limit,
                pipewire_stream_normalize_enabled=config.music_pipewire_stream_normalize_enabled,
                pipewire_stream_target_percent=config.music_pipewire_stream_target_percent,
            )

            # Route music audio based on current browser-audio control state.
            # Note: web_service not yet initialized at this point, so default to local routing.
            # It will be updated later if browser audio is enabled.
            try:
                browser_audio_enabled = web_service and web_service._ui_control_state.get("browser_audio_enabled", True)
            except NameError:
                browser_audio_enabled = False
            
            if browser_audio_enabled:
                music_pool.set_output_route("browser")
                music_control_pool.set_output_route("browser")
            else:
                music_pool.set_output_route("local")
                music_control_pool.set_output_route("local")
            
            # Check if library is empty and auto-update if needed
            stats = await music_manager.get_stats()
            song_count = int(stats.get("songs", 0))
            logger.info("Music library has %d songs", song_count)

            enabled_outputs = await music_manager.get_enabled_output_names()
            if enabled_outputs:
                logger.info("Enabled music outputs: %s", ", ".join(enabled_outputs))
                if len(enabled_outputs) == 1 and enabled_outputs[0].strip().lower() == "null output":
                    logger.warning(
                        "Music backend is using only Null Output (silent). Check orchestrator container audio routing (Pulse/ALSA)."
                    )
                elif len(enabled_outputs) > 1:
                    logger.warning(
                        "Music backend has multiple outputs enabled (%s). Ducking may sound inconsistent depending on active route; "
                        "prefer a single primary output with software mixer.",
                        ", ".join(enabled_outputs),
                    )
            else:
                logger.warning("Music backend reports no enabled outputs; playback will be silent")
            
            if song_count == 0:
                logger.info(
                    "Library is empty at startup; background index scan is running and songs will appear progressively"
                )
                print(
                    "  → Library is empty - background indexing is running (results will appear progressively)",
                    flush=True,
                )
            
            # Initialize music router
            music_router = MusicRouter(music_manager)
            
            logger.info("✓ Music Control System ready")
            print("✓ Music Control System ready", flush=True)
            
        except Exception as e:
            logger.error("Failed to initialize Music Control System: %s", e)
            logger.warning(
                "Music control disabled for this run due to backend initialization failure."
            )
            print(f"✗ Music Control System initialization failed: {e}", flush=True)
            music_router = None
            music_manager = None
    
    recorder_tool = None
    recording_blocks_tools = False
    if config.recorder_enabled:
        pyannote_client = None
        if config.recorder_pyannote_enabled and config.recorder_pyannote_url:
            pyannote_client = PyannoteClient(
                base_url=config.recorder_pyannote_url,
                model_id=config.recorder_pyannote_model,
                auth_token=config.recorder_pyannote_auth_token,
            )

        async def _on_recorder_started() -> None:
            nonlocal recording_blocks_tools, wake_state, wake_sleep_ts, last_wake_detected_ts, chunk_frames, chunk_start_ts, last_speech_ts
            nonlocal recorder_stop_hotword_armed_ts
            recording_blocks_tools = True
            wake_state = WakeState.ASLEEP
            wake_sleep_ts = time.monotonic()
            last_wake_detected_ts = None
            recorder_stop_hotword_armed_ts = None
            chunk_frames = []
            chunk_start_ts = None
            last_speech_ts = None
            ring_buffer.clear()
            web_service.update_orchestrator_status(recorder_active=True)
            logger.info("🎙️ Recorder started: enforcing quiet mode")
            if music_manager:
                try:
                    stop_result = await music_manager.stop()
                    if isinstance(stop_result, str) and stop_result.lower().startswith("error"):
                        logger.debug("Recorder start music stop returned: %s", stop_result)
                    else:
                        logger.info("🎵 Recorder started: stopped music playback")
                except Exception as exc:
                    logger.debug("Recorder start music stop failed: %s", exc)

            if alarm_manager:
                try:
                    stopped = await alarm_manager.stop_alarm(None)
                    if stopped > 0:
                        logger.info("🔕 Recorder started: silenced %d ringing alarm(s)", stopped)
                except Exception as exc:
                    logger.debug("Recorder start alarm stop failed: %s", exc)

        async def _on_recorder_stopped() -> None:
            nonlocal recording_blocks_tools, wake_state, wake_sleep_ts, last_wake_detected_ts
            nonlocal recorder_stop_hotword_armed_ts
            nonlocal last_timeout_progress_log_ts
            nonlocal state
            recording_blocks_tools = False
            wake_state = WakeState.ASLEEP
            state = VoiceState.IDLE
            wake_sleep_ts = time.monotonic()
            last_wake_detected_ts = None
            recorder_stop_hotword_armed_ts = None
            last_timeout_progress_log_ts = 0.0
            if web_service:
                web_service.update_ui_control_state(mic_enabled=False)
                web_service.update_orchestrator_status(
                    recorder_active=False,
                    wake_state=wake_state.value,
                    voice_state=state.value,
                    mic_enabled=False,
                    speech_active=False,
                )
            if wake_detector and hasattr(wake_detector, 'reset_state'):
                try:
                    wake_detector.reset_state()
                except Exception:
                    pass
            if timeout_swoosh_sound:
                try:
                    play_feedback_async(
                        timeout_swoosh_sound,
                        float(max(0.1, config.sleep_feedback_gain)),
                        "sleep swoosh (recorder stop)",
                    )
                except Exception as exc:
                    logger.debug("Failed to play sleep swoosh (recorder stop): %s", exc)
            logger.info("🎙️ Recorder stopped: timer/alarm processing resumes and system forced to sleep")

        print("→ Initializing Recorder tool...", flush=True)
        logger.info("→ Initializing Recorder tool...")
        recorder_tool = RecorderTool(
            workspace_root=workspace_root,
            output_dir=config.recorder_output_dir,
            sample_rate=config.audio_sample_rate,
            whisper_client=whisper_client,
            pyannote_client=pyannote_client,
            pyannote_enabled=config.recorder_pyannote_enabled,
            pyannote_auth_token=config.recorder_pyannote_auth_token,
            pyannote_model=config.recorder_pyannote_model,
            on_recording_started=_on_recorder_started,
            on_recording_stopped=_on_recorder_stopped,
        )
        logger.info("✓ Recorder tool ready (output_dir=%s)", workspace_root / config.recorder_output_dir)
        print("✓ Recorder tool ready", flush=True)

    # Embedded realtime web UI service handle (initialized later)
    web_service = None
    recordings_catalog = None

    # Media Key Detector (optional - works without music system, but won't control music)
    media_key_detector = None
    if config.media_keys_enabled:
        print("→ Initializing Media Key Detector...", flush=True)
        logger.info("→ Initializing Media Key Detector...")
        try:
            from orchestrator.audio.media_keys import MediaKeyDetector, MediaKeyEvent
            
            # Create detector with optional device filter
            device_filter = config.media_keys_device_filter if config.media_keys_device_filter else None
            media_key_detector = MediaKeyDetector(
                device_filter=device_filter,
                play_scan_codes=config.media_keys_play_scan_codes,
                mute_scan_codes=config.media_keys_mute_scan_codes,
                phone_scan_codes=config.media_keys_phone_scan_codes,
                command_debounce_ms=config.media_keys_command_debounce_ms,
                exclusive_grab=config.media_keys_exclusive_grab,
                passthrough_keys=config.media_keys_passthrough_keys,
            )

            alsa_card_cache: dict[str, str | None] = {}

            def _resolve_alsa_card_for_device(device_name: str) -> str | None:
                explicit_card = str(config.media_keys_alsa_card or "").strip()
                if explicit_card:
                    return explicit_card

                key = (device_name or "").strip().lower()
                if key in alsa_card_cache:
                    return alsa_card_cache[key]

                try:
                    out = subprocess.run(
                        ["arecord", "-l"],
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    ).stdout
                except Exception:
                    alsa_card_cache[key] = None
                    return None

                card_id: str | None = None
                for line in out.splitlines():
                    m = re.search(r"card\s+(\d+):\s+([^\[]+)", line, flags=re.IGNORECASE)
                    if not m:
                        continue
                    idx = m.group(1)
                    title = m.group(2).strip().lower()
                    if key and key in title:
                        card_id = idx
                        break
                    # Friendly fallback for conference speaker naming differences.
                    if "anker" in key and "powerconf" in title:
                        card_id = idx
                        break

                alsa_card_cache[key] = card_id
                return card_id

            async def _sync_hardware_mic_switch(muted: bool, device_name: str) -> None:
                if not config.media_keys_sync_alsa_mic_switch:
                    return

                card = _resolve_alsa_card_for_device(device_name)
                if not card:
                    return

                control = (config.media_keys_alsa_mic_control or "Mic").strip() or "Mic"
                switch_value = "nocap" if muted else "cap"

                def _run_amixer() -> None:
                    subprocess.run(
                        ["amixer", "-c", str(card), "sset", control, switch_value],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                try:
                    await asyncio.to_thread(_run_amixer)
                    logger.info(
                        "🎛️ ALSA mic switch sync: card=%s control=%s -> %s",
                        card,
                        control,
                        switch_value,
                    )
                except Exception as e:
                    logger.debug("ALSA mic switch sync failed: %s", e)
            
            # Set up callback to handle button presses
            async def on_media_key_press(event: MediaKeyEvent):
                """Handle media key button presses from hardware devices."""
                nonlocal wake_state, wake_sleep_ts, last_wake_detected_ts, last_activity_ts
                nonlocal capture, tts_stop_event, music_paused_for_wake, music_auto_resume_timer, music_was_playing
                nonlocal state, pending_transcripts, debounce_task, chunk_frames, chunk_start_ts, last_speech_ts
                nonlocal cut_in_triggered_ts
                nonlocal warned_missing_playerctl
                nonlocal tts_base_gain, tts_gain
                
                logger.info("Media key pressed: %s from %s", event.key, event.device_name)

                async def pause_music_for_wake(source_label: str) -> bool:
                    nonlocal music_paused_for_wake, music_auto_resume_timer

                    if not (config.music_enabled and music_manager):
                        return False

                    try:
                        paused = await music_manager.pause_if_playing()
                        if paused:
                            logger.info("🎵 Pausing music for %s", source_label)
                            music_paused_for_wake = True
                            music_auto_resume_timer = 0.0
                            return True
                    except Exception as e:
                        logger.debug("Error pausing music for %s: %s", source_label, e)

                    return False

                async def pause_system_media_if_needed(source_label: str):
                    nonlocal warned_missing_playerctl
                    if not config.media_keys_suppress_system_play:
                        return
                    if not shutil.which("playerctl"):
                        if not warned_missing_playerctl:
                            logger.warning(
                                "MEDIA_KEYS_SUPPRESS_SYSTEM_PLAY=true but playerctl is not installed; "
                                "set MEDIA_KEYS_EXCLUSIVE_GRAB=true to prevent VLC/OS playback toggles."
                            )
                            warned_missing_playerctl = True
                        return
                    try:
                        await asyncio.to_thread(
                            subprocess.run,
                            ["playerctl", "-a", "pause"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        logger.info("⏸️ Suppressed system media playback for %s", source_label)
                    except Exception as e:
                        logger.debug("Failed to suppress system media playback for %s: %s", source_label, e)

                async def restore_music_if_needed(source_label: str):
                    nonlocal music_paused_for_wake, music_auto_resume_timer, music_was_playing

                    if not music_paused_for_wake or not (config.music_enabled and music_manager):
                        return

                    try:
                        logger.info("🎵 Restoring paused music after %s", source_label)
                        await music_manager.play()
                        music_paused_for_wake = False
                        music_auto_resume_timer = 0.0
                        music_was_playing = True
                    except Exception as e:
                        logger.debug("Error restoring music after %s: %s", source_label, e)

                def play_feedback_async(
                    pcm: bytes | None,
                    gain: float,
                    label: str,
                    stop_event: threading.Event | None = None,
                ) -> None:
                    """Play short cues in background with explicit error logging."""
                    if not pcm:
                        return

                    if web_service and web_service.has_active_client():
                        _audio_authority = str(getattr(config, "web_ui_audio_authority", "native") or "native").lower()
                        if _audio_authority in ("browser", "hybrid"):
                            _wav = pcm if pcm[:4] == b"RIFF" else pcm_to_wav_bytes(pcm, config.audio_sample_rate)
                            web_service.send_feedback_sound(_wav, gain)

                    async def _runner() -> None:
                        try:
                            await asyncio.to_thread(
                                playback.play_pcm,
                                pcm,
                                float(gain),
                                stop_event if stop_event is not None else threading.Event(),
                            )
                        except Exception as exc:
                            logger.error("Failed to play %s: %s", label, exc)

                    asyncio.create_task(_runner())

                async def adjust_output_volume(direction: int):
                    nonlocal tts_base_gain, tts_gain

                    previous_tts_base_gain = tts_base_gain
                    tts_base_gain = max(0.2, min(3.0, tts_base_gain + (0.12 * direction)))

                    # Preserve cut-in ducking if active, otherwise keep live playback in sync.
                    if abs(tts_gain - previous_tts_base_gain) < 1e-6:
                        tts_gain = tts_base_gain
                    else:
                        tts_gain = min(tts_gain, tts_base_gain)

                    logger.info(
                        "🔊 TTS base gain %s to %.2f",
                        "increased" if direction > 0 else "decreased",
                        tts_base_gain,
                    )

                    if config.music_enabled and music_manager:
                        try:
                            if direction > 0:
                                result = await music_manager.increase_volume(5)
                            else:
                                result = await music_manager.decrease_volume(5)
                            logger.info("🎵 Music volume update: %s", result)
                        except Exception as e:
                            logger.debug("Failed to adjust music volume: %s", e)

                    # Play feedback click proportional to current volume level.
                    if volume_click_sound:
                        try:
                            normalized_level = (tts_base_gain - 0.2) / max(0.01, 3.0 - 0.2)
                            base_click_gain = float(max(0.0, config.volume_feedback_gain))
                            feedback_gain = float(min(3.0, base_click_gain * (0.75 + 0.5 * normalized_level)))
                            play_feedback_async(volume_click_sound, feedback_gain, "volume feedback click")
                        except Exception as e:
                            logger.debug("Failed to play volume feedback click: %s", e)

                async def trigger_wake(source_label: str):
                    nonlocal wake_state, wake_sleep_ts, last_wake_detected_ts, last_activity_ts
                    nonlocal music_paused_for_wake, state

                    logger.info("🎙️ %s - triggering wake word sequence", source_label)
                    tts_stop_event.set()

                    if capture and hasattr(capture, 'is_muted') and capture.is_muted():
                        capture.set_muted(False)
                        logger.info("🎤 Microphone unmuted for %s", source_label)

                    wake_state = WakeState.AWAKE
                    wake_sleep_ts = None
                    last_wake_detected_ts = time.monotonic()
                    if web_service:
                        web_service.note_hotword_detected()
                    last_activity_ts = time.monotonic()
                    state = VoiceState.LISTENING
                    logger.info(
                        "🎙️ System woken by %s | timeout=%dms | listening for transcription | mic_btn=green",
                        source_label, config.wake_word_timeout_ms,
                    )
                    if web_service:
                        web_service.update_ui_control_state(mic_enabled=True)
                        web_service.update_orchestrator_status(
                            wake_state=wake_state.value, voice_state=state.value
                        )

                    if wake_click_sound:
                        try:
                            play_feedback_async(
                                wake_click_sound,
                                float(max(0.1, config.wake_feedback_gain)),
                                "wake click",
                            )
                        except Exception as e:
                            logger.debug("Failed to play wake click: %s", e)

                    # Pause music in background so wake click and state transition are immediate.
                    if config.music_enabled and music_manager:
                        asyncio.create_task(pause_music_for_wake(source_label))

                async def trigger_sleep(source_label: str):
                    nonlocal wake_state, wake_sleep_ts, last_wake_detected_ts, last_activity_ts
                    nonlocal state, pending_transcripts, debounce_task, chunk_frames, chunk_start_ts
                    nonlocal last_speech_ts, cut_in_triggered_ts, music_auto_resume_timer

                    logger.info("😴 %s - putting system to sleep", source_label)

                    tts_stop_event.set()

                    drained_tts = 0
                    while True:
                        if not _tts_has_pending():
                            break
                        tts_queue.popleft()
                        drained_tts += 1

                    if not _tts_has_pending():
                        tts_queue_event.clear()

                    if drained_tts:
                        logger.info("🧹 Cleared %d queued TTS item(s) for sleep", drained_tts)

                    buffered_pcm: bytes | None = None
                    buffered_chunk_started_ts = chunk_start_ts
                    buffered_cut_in_ts = cut_in_triggered_ts
                    preserve_transcript_work = bool(pending_transcripts)

                    if chunk_frames and chunk_start_ts is not None:
                        buffered_pcm = b"".join(chunk_frames)
                        preserve_transcript_work = True
                        logger.info(
                            "🎤 Preserving buffered speech on %s sleep (%d frames, %d bytes)",
                            source_label,
                            len(chunk_frames),
                            len(buffered_pcm),
                        )

                    if preserve_transcript_work:
                        logger.info("📝 Preserving pending transcript work while transitioning to sleep")
                    else:
                        pending_transcripts.clear()
                        if debounce_task and not debounce_task.done():
                            debounce_task.cancel()
                        debounce_task = None

                    chunk_frames = []
                    chunk_start_ts = None
                    last_speech_ts = None
                    cut_in_triggered_ts = None
                    ring_buffer.clear()
                    music_auto_resume_timer = 0.0
                    state = VoiceState.IDLE

                    wake_state = WakeState.ASLEEP
                    wake_sleep_ts = time.monotonic()
                    last_wake_detected_ts = None
                    last_activity_ts = wake_sleep_ts

                    if wake_detector and hasattr(wake_detector, 'reset_state'):
                        try:
                            wake_detector.reset_state()
                        except Exception as e:
                            logger.debug("Failed to reset wake detector on %s sleep: %s", source_label, e)

                    if timeout_swoosh_sound:
                        try:
                            play_feedback_async(
                                timeout_swoosh_sound,
                                float(max(0.1, config.sleep_feedback_gain)),
                                "sleep swoosh",
                            )
                        except Exception as e:
                            logger.debug("Failed to play sleep swoosh: %s", e)

                    if buffered_pcm:
                        asyncio.create_task(
                            process_chunk(
                                buffered_pcm,
                                buffered_cut_in_ts,
                                buffered_chunk_started_ts,
                            )
                        )

                    await restore_music_if_needed(source_label)
                    logger.info("😴 System put to sleep by %s | mic_btn=red", source_label)
                    if web_service:
                        web_service.update_ui_control_state(mic_enabled=False)
                        web_service.update_orchestrator_status(
                            wake_state=wake_state.value, voice_state=state.value,
                            mic_enabled=False,
                        )
                
                # Mute button -> force zero microphone input via capture mute.
                if event.key == "mute":
                    if capture and hasattr(capture, "toggle_mute"):
                        new_state = capture.toggle_mute()
                        await _sync_hardware_mic_switch(new_state, event.device_name)
                        logger.info(
                            "🎤 Microphone %s via hardware mute button on %s",
                            "muted" if new_state else "unmuted",
                            event.device_name,
                        )
                    else:
                        logger.info("🎤 Mute button pressed on %s but capture mute is unavailable", event.device_name)
                    return
                
                # Play button (or mapped MSC_SCAN play) → Toggle wake/sleep
                elif event.key in ("play_pause", "play", "pause"):
                    if recording_blocks_tools and recorder_tool and recorder_tool.is_recording():
                        logger.info("🎛️ Play button stopping active recording")
                        stop_result = await recorder_tool.stop_recording(reason="play button")
                        _append_recorder_finished_chat(stop_result)
                        if stop_result.response:
                            await submit_tts(stop_result.response, request_id=current_request_id, kind="notification")
                        return

                    dismissed = await stop_ringing_alarms_immediately("play button")
                    if dismissed > 0:
                        tts_stop_event.set()
                        _tts_clear_all("alarm dismissed by play button")
                        logger.info("🎛️ Play button consumed by alarm dismiss")
                        return

                    asyncio.create_task(pause_system_media_if_needed("play button"))
                    if wake_state == WakeState.AWAKE:
                        await trigger_sleep("play button")
                    else:
                        await trigger_wake("play button")
                
                # Next/Previous should behave exactly like play-toggle for button parity
                elif event.key in ("next", "previous"):
                    dismissed = await stop_ringing_alarms_immediately(f"{event.key} button")
                    if dismissed > 0:
                        tts_stop_event.set()
                        _tts_clear_all(f"alarm dismissed by {event.key} button")
                        logger.info("🎛️ %s button consumed by alarm dismiss", event.key)
                        return

                    asyncio.create_task(pause_system_media_if_needed(f"{event.key} button"))
                    if wake_state == WakeState.AWAKE:
                        await trigger_sleep(f"{event.key} button")
                    else:
                        await trigger_wake(f"{event.key} button")
                
                # Long press play button (0.5s+) → Alternative wake trigger (if supported)
                elif event.key == "play_pause_long":
                    asyncio.create_task(pause_system_media_if_needed("play button long-press"))
                    await trigger_wake("play button long-press")
                
                # Phone button → Trigger wake word (stop audio, notification sound, unmute mic, wake system)
                elif event.key == "phone":
                    asyncio.create_task(pause_system_media_if_needed("phone button"))
                    await trigger_wake("phone button")
                
                # Volume controls -> orchestrator-managed TTS + music volume.
                elif event.key == "volume_up":
                    await adjust_output_volume(1)

                elif event.key == "volume_down":
                    await adjust_output_volume(-1)

                # Standard media controls
                elif config.media_keys_control_music and music_manager:
                    if event.key == "stop":
                        asyncio.create_task(music_manager.stop())
                    else:
                        logger.debug("Unhandled media key: %s", event.key)
            
            media_key_detector.set_callback(on_media_key_press)
            
            # Start monitoring and report readiness only when devices are actually active
            await media_key_detector.start()
            if media_key_detector.devices:
                logger.info("✓ Media Key Detector ready (%d device(s))", len(media_key_detector.devices))
                print("✓ Media Key Detector ready", flush=True)
            else:
                logger.warning("Media Key Detector enabled but no devices are currently active")
                print("⚠ Media Key Detector enabled but no devices found", flush=True)
            
        except ImportError:
            logger.error("Media Key Detector requires 'evdev' library: pip install evdev")
            print("✗ Media Key Detector failed: evdev library not found", flush=True)
            media_key_detector = None
        except Exception as e:
            logger.error("Failed to initialize Media Key Detector: %s", e)
            print(f"✗ Media Key Detector initialization failed: {e}", flush=True)
            media_key_detector = None
    
    # Quick Answer LLM (optional)
    quick_answer_client = None
    if config.quick_answer_enabled:
        print("→ Initializing Quick Answer LLM...", flush=True)
        logger.info("→ Initializing Quick Answer LLM (%s)...", config.quick_answer_llm_url)
        try:
            from orchestrator.gateway.quick_answer import (
                QuickAnswerClient,
                check_openclaw_models_available,
                classify_upstream_decision,
                get_random_thinking_phrase,
                resolve_recommended_model_id,
            )

            # Check if OpenClaw gateway has configured models available
            openclaw_models_available = True
            from orchestrator.gateway.providers import OpenClawGateway
            if isinstance(gateway, OpenClawGateway):
                try:
                    model_config_candidates = [
                        str(Path.cwd() / "models.json"),
                        str(Path.cwd() / "openclaw.json"),
                        str(Path.cwd() / ".openclaw" / "models.json"),
                        str(Path.cwd() / ".openclaw" / "openclaw.json"),
                        str(Path.cwd().parent / ".openclaw" / "models.json"),
                        str(Path.cwd().parent / ".openclaw" / "openclaw.json"),
                        str(Path.home() / ".openclaw" / "models.json"),
                        str(Path.home() / ".openclaw" / "openclaw.json"),
                    ]
                    openclaw_models_available = await check_openclaw_models_available(
                        gateway.gateway_url,
                        gateway.token,
                        timeout_s=5.0,
                        config_paths=model_config_candidates,
                    )
                except Exception as exc:
                    logger.debug("Failed to check OpenClaw models availability: %s", exc)
                    openclaw_models_available = False

            quick_answer_client = QuickAnswerClient(
                llm_url=config.quick_answer_llm_url,
                model=config.quick_answer_model if config.quick_answer_model else None,
                api_key=config.quick_answer_api_key if config.quick_answer_api_key else None,
                timeout_ms=config.quick_answer_timeout_ms,
                timers_enabled=timers_feature_enabled,
                music_enabled=config.music_enabled,
                recorder_enabled=config.recorder_enabled,
                tool_router=tool_router,
                music_router=music_router,
                recorder_tool=recorder_tool,
                openclaw_models_available=openclaw_models_available,
            )
            if openclaw_models_available:
                logger.info("✓ Quick Answer LLM ready (with model tier resolution)")
                print("✓ Quick Answer LLM ready (with model tier resolution)", flush=True)
            else:
                logger.info("✓ Quick Answer LLM ready (model tier resolution disabled - no models configured)")
                print("✓ Quick Answer LLM ready (model tier resolution disabled)", flush=True)
        except ImportError as ie:
            logger.error("Quick Answer disabled: missing dependency (%s)", ie)
            logger.error("Install dependencies with: pip install -r requirements.txt")
            print("✗ Quick Answer disabled: missing dependency (install requirements.txt)", flush=True)
            quick_answer_client = None
        except Exception as e:
            logger.error("Quick Answer initialization failed: %s", e)
            print(f"✗ Quick Answer initialization failed: {e}", flush=True)
            quick_answer_client = None
    
    # TTS client
    print("→ Initializing Piper TTS client...", flush=True)
    logger.info("→ Initializing Piper TTS client (%s)...", config.piper_url)
    piper_start = time.monotonic()
    piper = PiperClient(config.piper_url)
    piper_elapsed = int((time.monotonic() - piper_start) * 1000)
    logger.info("✓ Piper client ready in %dms", piper_elapsed)
    print(f"✓ Piper client ready in {piper_elapsed}ms", flush=True)
    if config.audio_backend != "portaudio-duplex":
        playback_rate = selected_playback_rate
        pi_keepalive = bool(config.audio_playback_keepalive_enabled and is_raspberry_pi())
        if config.audio_playback_keepalive_enabled and not pi_keepalive:
            logger.info("Playback keepalive requested but disabled (non-Pi system)")
        elif pi_keepalive:
            logger.info(
                "Playback keepalive enabled for Pi (interval=%dms)",
                config.audio_playback_keepalive_interval_ms,
            )
        playback = AudioPlayback(
            sample_rate=playback_rate,
            device=selected_playback_device,
            lead_in_ms=config.audio_playback_lead_in_ms,
            keepalive_enabled=pi_keepalive,
            keepalive_interval_ms=config.audio_playback_keepalive_interval_ms,
        )
    
    # Generate audio feedback sounds
    logger.info("→ Generating audio feedback sounds...")
    wake_variant = (config.wake_feedback_variant or "click").strip().lower()
    if wake_variant == "knocklow":
        wake_click_sound = generate_knock_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=78,
            base_frequency=210,
        )
    elif wake_variant == "knock":
        wake_click_sound = generate_knock_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=72,
            base_frequency=250,
        )
    elif wake_variant == "doubleknock":
        knock_a = wav_bytes_to_pcm(
            generate_knock_sound(
                sample_rate=config.audio_sample_rate,
                duration_ms=66,
                base_frequency=250,
            )
        )
        knock_b = wav_bytes_to_pcm(
            generate_knock_sound(
                sample_rate=config.audio_sample_rate,
                duration_ms=70,
                base_frequency=220,
            )
        )
        gap = bytes(int(config.audio_sample_rate * 0.055) * 2)
        wake_click_sound = pcm_to_wav_bytes(knock_a + gap + knock_b, config.audio_sample_rate)
    elif wake_variant == "cluck":
        wake_click_sound = generate_cluck_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=92,
            base_frequency=430,
        )
    elif wake_variant == "doublecluck":
        cluck_a = wav_bytes_to_pcm(
            generate_cluck_sound(
                sample_rate=config.audio_sample_rate,
                duration_ms=82,
                base_frequency=420,
            )
        )
        cluck_b = wav_bytes_to_pcm(
            generate_cluck_sound(
                sample_rate=config.audio_sample_rate,
                duration_ms=88,
                base_frequency=380,
            )
        )
        gap = bytes(int(config.audio_sample_rate * 0.06) * 2)
        wake_click_sound = pcm_to_wav_bytes(cluck_a + gap + cluck_b, config.audio_sample_rate)
    elif wake_variant == "double":
        click_a = wav_bytes_to_pcm(
            generate_click_sound(sample_rate=config.audio_sample_rate, duration_ms=12, frequency=1900)
        )
        click_b = wav_bytes_to_pcm(
            generate_click_sound(sample_rate=config.audio_sample_rate, duration_ms=14, frequency=2300)
        )
        gap = bytes(int(config.audio_sample_rate * 0.05) * 2)
        wake_click_sound = pcm_to_wav_bytes(click_a + gap + click_b, config.audio_sample_rate)
    elif wake_variant == "bright":
        wake_click_sound = generate_click_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=18,
            frequency=2800,
        )
    elif wake_variant == "soft":
        wake_click_sound = generate_click_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=20,
            frequency=1300,
        )
    else:
        wake_click_sound = generate_click_sound(sample_rate=config.audio_sample_rate, duration_ms=12, frequency=2000)

    sleep_variant = (config.sleep_feedback_variant or "swoosh").strip().lower()
    if sleep_variant == "none":
        timeout_swoosh_sound = None
    elif sleep_variant == "exhale":
        timeout_swoosh_sound = generate_exhale_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=660,
            brightness=0.24,
        )
    elif sleep_variant == "exhaleshort":
        timeout_swoosh_sound = generate_exhale_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=460,
            brightness=0.28,
        )
    elif sleep_variant == "exhalelong":
        timeout_swoosh_sound = generate_exhale_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=1320,
            brightness=0.22,
        )
    elif sleep_variant == "sigh":
        timeout_swoosh_sound = generate_sigh_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=560,
            start_frequency=520,
            end_frequency=110,
        )
    elif sleep_variant == "sighshort":
        timeout_swoosh_sound = generate_sigh_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=420,
            start_frequency=560,
            end_frequency=140,
        )
    elif sleep_variant == "short":
        timeout_swoosh_sound = generate_swoosh_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=180,
            start_frequency=900,
            end_frequency=260,
        )
    elif sleep_variant == "deep":
        timeout_swoosh_sound = generate_swoosh_sound(
            sample_rate=config.audio_sample_rate,
            duration_ms=340,
            start_frequency=650,
            end_frequency=120,
        )
    else:
        timeout_swoosh_sound = generate_swoosh_sound(sample_rate=config.audio_sample_rate)

    volume_click_sound = generate_click_sound(
        sample_rate=config.audio_sample_rate,
        duration_ms=10,
        frequency=3000,
    )
    if volume_click_sound and volume_click_sound.startswith(b"RIFF"):
        volume_click_sound = wav_bytes_to_pcm(volume_click_sound)

    def play_feedback_async(
        pcm: bytes | None,
        gain: float,
        label: str,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Play short cues in background with explicit error logging."""
        if not pcm:
            return

        if web_service and web_service.has_active_client():
            _audio_authority = str(getattr(config, "web_ui_audio_authority", "native") or "native").lower()
            if _audio_authority in ("browser", "hybrid"):
                _wav = pcm if pcm[:4] == b"RIFF" else pcm_to_wav_bytes(pcm, config.audio_sample_rate)
                web_service.send_feedback_sound(_wav, gain)

        async def _runner() -> None:
            try:
                await asyncio.to_thread(
                    playback.play_pcm,
                    pcm,
                    float(gain),
                    stop_event if stop_event is not None else threading.Event(),
                )
            except Exception as exc:
                logger.error("Failed to play %s: %s", label, exc)

        asyncio.create_task(_runner())

    aec = WebRTCAEC(
        sample_rate=config.audio_sample_rate,
        frame_ms=config.audio_frame_ms,
        strength=config.echo_cancel_strength,
    ) if config.echo_cancel else None

    aec_status = AECStatus(
        enabled=bool(config.echo_cancel),
        backend="webrtc_audio_processing",
        available=aec is not None,
    )
    logger.info("✓ AEC: enabled=%s backend=%s available=%s", aec_status.enabled, aec_status.backend, aec_status.available)

    tts_queue: deque[TTSQueueItem] = deque()
    tts_queue_event = asyncio.Event()
    tts_stop_event = threading.Event()
    alarm_playback_stop_event = threading.Event()
    tts_playback_start_ts: float | None = None
    current_tts_text = ""
    current_tts_duration_s = 0.0
    
    print("\n" + "="*51, flush=True)
    print("  ✓ System Ready - All models loaded", flush=True)
    print(f"  Session: {session_id} | Agent: {agent_id}", flush=True)
    print("="*51 + "\n", flush=True)
    
    logger.info("═══════════════════════════════════════════════════")
    logger.info("  ✓ System Ready - All models loaded")
    logger.info("  Session: %s | Agent: %s", session_id, agent_id)
    logger.info("═══════════════════════════════════════════════════")

    def playback_callback(pcm: bytes) -> None:
        nonlocal last_playback_frame
        last_playback_frame = pcm

    playback.set_playback_callback(playback_callback)

    # Debounce state for transcript aggregation
    pending_transcripts: list[tuple[str, str]] = []  # (transcript, emotion_tag)
    debounce_task: asyncio.Task | None = None
    processing_request = False  # Flag to prevent concurrent gateway requests
    web_service = None
    new_session_reset_task: asyncio.Task | None = None

    async def _send_gateway_session_reset(source: str) -> None:
        try:
            await gateway.send_message(
                "/reset",
                session_id=session_id,
                agent_id=agent_id,
                metadata={"source": source},
            )
            logger.info("✓ Gateway session reset via /reset")
        except asyncio.CancelledError:
            logger.info("↪ Gateway /reset task cancelled (source=%s)", source)
            raise
        except Exception as exc:
            logger.warning("Gateway /reset failed (continuing with local reset): %s", exc)

    async def _start_new_session(*, source: str, client_id: str | None = None) -> str:
        nonlocal pending_transcripts, debounce_task, processing_request, current_request_id
        nonlocal suppress_gateway_messages_for_new_session
        nonlocal new_session_reset_task
        if client_id:
            logger.info("🆕 New session requested by %s (client=%s)", source, client_id)
        else:
            logger.info("🆕 New session requested by %s", source)
        try:
            pending_transcripts.clear()
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()
            debounce_task = None
            processing_request = False
            current_request_id += 1
            _force_close_gateway_collation_window(f"{source} new session")
            if config.new_session_suppress_welcome_message:
                suppress_gateway_messages_for_new_session = True
            _tts_clear_all(f"{source} new session")
            tts_stop_event.set()

            if web_service:
                web_service.start_new_chat()

            if new_session_reset_task and not new_session_reset_task.done():
                new_session_reset_task.cancel()
            new_session_reset_task = asyncio.create_task(_send_gateway_session_reset(source))

            return "Started a new session."
        finally:
            tts_stop_event.clear()

    if quick_answer_client:
        quick_answer_client.set_new_session_handler(
            lambda: _start_new_session(source="quick_answer", client_id=None)
        )

    if config.web_ui_enabled:
        print("→ Starting Embedded Voice Web UI...", flush=True)
        logger.info("→ Starting Embedded Voice Web UI service")
        try:
            from orchestrator.web import EmbeddedVoiceWebService
            from orchestrator.web.recordings_catalog import RecordingsCatalog

            def _on_recordings_changed(rows: list[dict[str, Any]]) -> None:
                if web_service:
                    web_service.update_recordings_state(rows)

            recordings_catalog = RecordingsCatalog(
                workspace_root / config.recorder_output_dir,
                on_change=_on_recordings_changed,
            )
            await recordings_catalog.start()

            web_service = EmbeddedVoiceWebService(
                host=config.web_ui_host,
                ui_port=config.web_ui_port,
                ws_port=config.web_ui_ws_port,
                status_hz=config.web_ui_status_hz,
                hotword_active_ms=config.web_ui_hotword_active_ms,
                mic_starts_disabled=config.web_ui_mic_starts_disabled,
                audio_authority=config.web_ui_audio_authority,
                chat_history_limit=config.web_ui_chat_history_limit,
                ssl_certfile=config.web_ui_ssl_certfile,
                ssl_keyfile=config.web_ui_ssl_keyfile,
                http_redirect_port=config.web_ui_http_redirect_port,
                chat_persist_path=config.web_ui_chat_persist_path,
                static_root=config.web_ui_static_root,
                workspace_files_enabled=config.web_ui_workspace_files_enabled,
                workspace_files_root=config.web_ui_workspace_files_root,
                workspace_files_allow_listing=config.web_ui_workspace_files_allow_listing,
                media_files_enabled=config.web_ui_media_files_enabled,
                media_files_root=config.web_ui_media_files_root,
                media_files_allow_listing=config.web_ui_media_files_allow_listing,
                openclaw_workspace_root=str(workspace_root),
                auth_mode=config.web_ui_auth_mode,
                google_client_id=config.web_ui_google_client_id,
                google_client_secret=config.web_ui_google_client_secret,
                google_client_secret_file=config.web_ui_google_client_secret_file,
                google_redirect_uri=config.web_ui_google_redirect_uri,
                google_allowed_domain=config.web_ui_google_allowed_domain,
                google_allowed_users=config.web_ui_google_allowed_users,
                auth_session_cookie_name=config.web_ui_auth_session_cookie_name,
                auth_session_ttl_hours=config.web_ui_auth_session_ttl_hours,
                auth_cookie_secure=config.web_ui_auth_cookie_secure,
                file_manager_enabled=config.web_ui_file_manager_enabled,
                file_manager_root=config.web_ui_file_manager_root,
                file_manager_excluded_folders=config.web_ui_file_manager_excluded_folders,
                file_manager_top_level_config_files=config.web_ui_file_manager_top_level_config_files,
                file_manager_max_editable_bytes=config.web_ui_file_manager_max_editable_bytes,
            )
            await web_service.start()
            web_service.update_recordings_state(recordings_catalog.list_recordings())
            web_service.update_orchestrator_status(
                voice_state=state.value,
                wake_state=wake_state.value,
                speech_active=False,
                tts_playing=False,
                mic_rms=0.0,
                queue_depth=0,
            )
            ui_scheme = "https" if config.web_ui_ssl_certfile and config.web_ui_ssl_keyfile else "http"
            ws_scheme = "wss" if ui_scheme == "https" else "ws"
            logger.info(
                "✓ Embedded Voice Web UI ready at %s://%s:%d (%s://%s:%d)",
                ui_scheme,
                config.web_ui_host,
                config.web_ui_port,
                ws_scheme,
                config.web_ui_host,
                config.web_ui_ws_port,
            )
            if config.web_ui_http_redirect_port:
                logger.info(
                    "✓ Embedded Voice Web UI HTTP redirector ready at http://%s:%d",
                    config.web_ui_host,
                    config.web_ui_http_redirect_port,
                )
            print(
                f"✓ Embedded Voice Web UI ready at {ui_scheme}://{config.web_ui_host}:{config.web_ui_port}",
                flush=True,
            )

            # Register web UI action handlers
            async def _ui_mic_toggle(client_id: str) -> None:
                nonlocal wake_state, state, wake_sleep_ts, last_wake_detected_ts, last_activity_ts

                if recording_blocks_tools and recorder_tool and recorder_tool.is_recording():
                    logger.info("🎛️ Web mic button stopping active recording")
                    stop_result = await recorder_tool.stop_recording(reason="mic button")
                    _append_recorder_finished_chat(stop_result)
                    if stop_result.response:
                        await submit_tts(stop_result.response, request_id=current_request_id, kind="notification")
                    return

                dismissed = await stop_ringing_alarms_immediately("web ui mic button")
                if dismissed > 0:
                    tts_stop_event.set()
                    return

                def _play_wake_feedback() -> None:
                    if not wake_click_sound:
                        return
                    try:
                        play_feedback_async(
                            wake_click_sound,
                            float(max(0.1, config.wake_feedback_gain)),
                            "wake click (web mic)",
                        )
                    except Exception as exc:
                        logger.debug("Failed to play wake click (web mic): %s", exc)

                def _play_sleep_feedback() -> None:
                    if not timeout_swoosh_sound:
                        return
                    try:
                        play_feedback_async(
                            timeout_swoosh_sound,
                            float(max(0.1, config.sleep_feedback_gain)),
                            "sleep swoosh (web mic)",
                        )
                    except Exception as exc:
                        logger.debug("Failed to play sleep swoosh (web mic): %s", exc)

                cur_mic = web_service._ui_control_state.get("mic_enabled", False)
                is_awake = wake_state == WakeState.AWAKE
                if is_awake or cur_mic:
                    web_service.update_ui_control_state(mic_enabled=False)
                    wake_state = WakeState.ASLEEP
                    wake_sleep_ts = time.monotonic()
                    last_wake_detected_ts = None
                    state = VoiceState.IDLE
                    _play_sleep_feedback()
                else:
                    web_service.update_ui_control_state(mic_enabled=True)
                    web_service.note_hotword_detected()
                    wake_state = WakeState.AWAKE
                    wake_sleep_ts = None
                    last_wake_detected_ts = time.monotonic()
                    last_activity_ts = time.monotonic()
                    state = VoiceState.LISTENING
                    _play_wake_feedback()
                    if config.music_enabled and music_manager:
                        asyncio.create_task(music_manager.pause_if_playing())
                web_service.update_orchestrator_status(
                    wake_state=wake_state.value, voice_state=state.value,
                    mic_enabled=web_service._ui_control_state.get("mic_enabled", False),
                )

            async def _ui_tts_mute_set(enabled: bool, client_id: str) -> None:
                nonlocal tts_playing
                web_service.update_ui_control_state(tts_muted=bool(enabled))
                if enabled:
                    tts_stop_event.set()
                    tts_playing = False

            def _sync_music_output_route(reason: str) -> None:
                if not music_manager or not web_service:
                    return
                if not hasattr(music_manager, "pool") or not hasattr(music_manager.pool, "set_output_route"):
                    return
                browser_audio_enabled = bool(web_service._ui_control_state.get("browser_audio_enabled", True))
                target_route = "browser" if browser_audio_enabled else "local"
                try:
                    music_manager.pool.set_output_route(target_route)
                    if getattr(music_manager, "control_pool", None) is not None and hasattr(music_manager.control_pool, "set_output_route"):
                        music_manager.control_pool.set_output_route(target_route)
                except Exception as exc:
                    logger.debug("Failed to sync native music output route (%s): %s", reason, exc)

            async def _ui_browser_audio_set(enabled: bool, client_id: str) -> None:
                prev_enabled = bool(web_service._ui_control_state.get("browser_audio_enabled", True))
                web_service.update_ui_control_state(browser_audio_enabled=bool(enabled))
                _sync_music_output_route("browser_audio_set")
                if not music_manager or prev_enabled == bool(enabled):
                    return

                # Seamless route handoff: keep the same song/time when toggling
                # browser-audio mode so only one output path is active.
                try:
                    status_before = await music_manager.get_status()
                except Exception as exc:
                    logger.debug("Browser-audio handoff status fetch failed: %s", exc)
                    return

                state_before = str((status_before or {}).get("state", "stop") or "stop").strip().lower()
                if state_before not in {"play", "pause"}:
                    return

                try:
                    song_pos = int((status_before or {}).get("song", -1))
                except Exception:
                    song_pos = -1
                if song_pos < 0:
                    return

                try:
                    elapsed_s = max(0, int(float((status_before or {}).get("elapsed", 0) or 0)))
                except Exception:
                    elapsed_s = 0

                try:
                    await music_manager.play(song_pos)
                    if elapsed_s > 0:
                        await music_manager.seek_to(float(elapsed_s))
                    if state_before == "pause":
                        await music_manager.pause()
                except Exception as exc:
                    logger.warning("Browser-audio handoff failed (target=%s): %s", "browser" if bool(enabled) else "local", exc)

            async def _ui_continuous_mode_set(enabled: bool, client_id: str) -> None:
                nonlocal wake_state, state, last_wake_detected_ts, last_activity_ts, wake_sleep_ts
                on = bool(enabled)
                web_service.update_ui_control_state(continuous_mode=on)
                if on:
                    web_service.update_ui_control_state(mic_enabled=True)
                    wake_state = WakeState.AWAKE
                    state = VoiceState.LISTENING
                    wake_sleep_ts = None
                    last_wake_detected_ts = time.monotonic()
                    last_activity_ts = last_wake_detected_ts
                    if web_service:
                        web_service.note_hotword_detected()
                    web_service.update_orchestrator_status(
                        wake_state=wake_state.value,
                        voice_state=state.value,
                        mic_enabled=True,
                    )

            # Event set by music_load_playlist handler to bypass queue-refresh backoff.
            # Cleared by _web_ui_publisher on each iteration after processing.
            _post_load_queue_event = asyncio.Event()

            async def _ui_refresh_music_state(source: str) -> None:
                """Refresh music state on UI.
                
                IMPORTANT: This function does NOT block on fetching the full queue.
                It publishes transport state (play/pause/elapsed) immediately (non-blocking),
                then fetches the queue asynchronously in the background.
                This ensures music control actions respond instantly (< 100ms).
                """
                if not music_manager or not web_service:
                    return
                try:
                    ms = await music_manager.get_ui_music_state()
                except Exception as exc:
                    logger.warning("Web UI %s transport refresh failed: %s", source, exc)
                    return

                # Publish transport state immediately (non-blocking) so UI responds instantly to actions
                web_service.update_music_transport(**ms)
                
                # Queue and playlists are refreshed by the web_ui_publisher when it detects
                # transport metadata (loaded_playlist, queue_length) changing. This avoids
                # concurrent queue fetches that can saturate the music connection pool on
                # large playlists.

            async def _ui_get_music_state_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
                if not music_manager:
                    return {"state": "stop", "queue_length": 0}, []

                transport = await music_manager.get_ui_music_state()
                # Do not force a fresh queue fetch for ad-hoc snapshot requests.
                # Large playlistinfo calls can take seconds and should be refreshed
                # by the async queue publisher instead of blocking this snapshot.
                queue = list(getattr(web_service, "_music_queue", []) or []) if web_service else []
                return transport, queue

            async def _ui_music_toggle(client_id: str) -> None:
                if music_manager:
                    try:
                        _sync_music_output_route("music_toggle")
                        await music_manager.toggle_playback()
                        asyncio.create_task(_ui_refresh_music_state("music_toggle"))
                    except Exception as exc:
                        logger.warning("Web UI music_toggle: %s", exc)

            async def _ui_music_stop(client_id: str) -> None:
                if music_manager:
                    try:
                        _sync_music_output_route("music_stop")
                        await music_manager.stop()
                        asyncio.create_task(_ui_refresh_music_state("music_stop"))
                    except Exception as exc:
                        logger.warning("Web UI music_stop: %s", exc)

            async def _ui_music_play_track(position: int, client_id: str) -> None:
                if music_manager:
                    try:
                        _sync_music_output_route("music_play_track")
                        await music_manager.play(position)
                        asyncio.create_task(_ui_refresh_music_state("music_play_track"))
                    except Exception as exc:
                        logger.warning("Web UI music_play_track pos=%d: %s", position, exc)

            async def _ui_music_seek(seconds: float, client_id: str) -> None:
                if music_manager:
                    try:
                        _sync_music_output_route("music_seek")
                        await music_manager.seek_to(seconds)
                        asyncio.create_task(_ui_refresh_music_state("music_seek"))
                    except Exception as exc:
                        logger.warning("Web UI music_seek seconds=%s: %s", seconds, exc)

            async def _ui_music_clear_queue(client_id: str) -> None:
                if music_manager:
                    try:
                        result = await music_manager.clear_queue()
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        asyncio.create_task(_ui_refresh_music_state("music_clear_queue"))
                    except Exception as exc:
                        logger.warning("Web UI music_clear_queue: %s", exc)

            async def _ui_music_remove_selected(
                positions: list[int],
                client_id: str,
                song_ids: list[str] | None = None,
            ) -> None:
                if music_manager:
                    try:
                        result = await music_manager.remove_from_queue_positions(positions, song_ids=song_ids)
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        loaded_playlist = music_manager.get_loaded_playlist_name()
                        if loaded_playlist:
                            save_result = await music_manager.save_playlist(loaded_playlist)
                            if str(save_result).strip().lower().startswith("error:"):
                                raise RuntimeError(save_result)
                        await _ui_refresh_music_state("music_remove_selected")
                    except Exception as exc:
                        logger.warning("Web UI music_remove_selected: %s", exc)

            async def _ui_music_add_files(files: list[str], client_id: str) -> None:
                if music_manager:
                    try:
                        result = await music_manager.add_files_to_queue(files)
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        loaded_playlist = music_manager.get_loaded_playlist_name()
                        if loaded_playlist:
                            save_result = await music_manager.save_playlist(loaded_playlist)
                            if str(save_result).strip().lower().startswith("error:"):
                                raise RuntimeError(save_result)
                        await _ui_refresh_music_state("music_add_files")
                    except Exception as exc:
                        logger.warning("Web UI music_add_files: %s", exc)

            async def _ui_music_create_playlist(name: str, positions: list[int], client_id: str) -> None:
                if music_manager:
                    try:
                        await music_manager.create_playlist_from_queue_positions(name, positions)
                        await _ui_refresh_music_state("music_create_playlist")
                    except Exception as exc:
                        logger.warning("Web UI music_create_playlist '%s': %s", name, exc)

            async def _ui_music_load_playlist(name: str, client_id: str) -> None:
                if music_manager:
                    try:
                        result = await music_manager.load_playlist(name)
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        # Emit a lightweight transport hint immediately so UI/voice clients
                        # observe the loaded playlist even if a full state snapshot is slow.
                        if web_service:
                            web_service.update_music_transport(loaded_playlist=name)
                        asyncio.create_task(_ui_refresh_music_state("music_load_playlist"))
                        # Signal the publisher to immediately refresh the queue,
                        # bypassing any existing backoff from previous failures.
                        _post_load_queue_event.set()
                    except Exception as exc:
                        logger.warning("Web UI music_load_playlist '%s': %s", name, exc)
                        raise

            async def _ui_music_save_playlist(name: str, client_id: str) -> None:
                if music_manager:
                    try:
                        await music_manager.save_playlist(name)
                        await _ui_refresh_music_state("music_save_playlist")
                    except Exception as exc:
                        logger.warning("Web UI music_save_playlist '%s': %s", name, exc)

            async def _ui_music_save_queue_then_clear_queue(save_name: str, client_id: str) -> None:
                if music_manager:
                    try:
                        save_result = await music_manager.save_playlist(save_name)
                        if str(save_result).strip().lower().startswith("error:"):
                            raise RuntimeError(save_result)
                        clear_result = await music_manager.clear_queue()
                        if str(clear_result).strip().lower().startswith("error:"):
                            raise RuntimeError(clear_result)
                        await _ui_refresh_music_state("music_save_queue_then_clear_queue")
                    except Exception as exc:
                        logger.warning("Web UI music_save_queue_then_clear_queue '%s': %s", save_name, exc)
                        raise

            async def _ui_music_save_queue_then_load_playlist(save_name: str, name: str, client_id: str) -> None:
                if music_manager:
                    try:
                        save_result = await music_manager.save_playlist(save_name)
                        if str(save_result).strip().lower().startswith("error:"):
                            raise RuntimeError(save_result)
                        result = await music_manager.load_playlist(name)
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        if web_service:
                            web_service.update_music_transport(loaded_playlist=name)
                        asyncio.create_task(_ui_refresh_music_state("music_save_queue_then_load_playlist"))
                        _post_load_queue_event.set()
                    except Exception as exc:
                        logger.warning("Web UI music_save_queue_then_load_playlist save='%s' load='%s': %s", save_name, name, exc)
                        raise

            async def _ui_music_rename_playlist(old_name: str, new_name: str, client_id: str) -> None:
                logger.info("[RENAME] Callback: old_name=%s, new_name=%s, client_id=%s", old_name, new_name, client_id)
                if music_manager:
                    try:
                        logger.info("[RENAME] Calling music_manager.rename_playlist()")
                        result = await music_manager.rename_playlist(old_name, new_name)
                        logger.info("[RENAME] Result: %s", result)
                        if str(result).strip().lower().startswith("error:"):
                            raise RuntimeError(result)
                        logger.info("[RENAME] Refreshing music state")
                        await _ui_refresh_music_state("music_rename_playlist")
                        logger.info("[RENAME] Refresh complete")
                    except Exception as exc:
                        logger.warning("Web UI music_rename_playlist '%s' -> '%s': %s", old_name, new_name, exc)
                        raise

            async def _ui_music_delete_playlist(name: str, client_id: str) -> None:
                if music_manager:
                    try:
                        await music_manager.delete_playlist(name)
                        await _ui_refresh_music_state("music_delete_playlist")
                    except Exception as exc:
                        logger.warning("Web UI music_delete_playlist '%s': %s", name, exc)

            async def _ui_music_search_library(query: str, limit: int, client_id: str) -> list[dict[str, Any]]:
                if not music_manager:
                    return []
                try:
                    api_start_ms = time.time() * 1000
                    safe_limit = max(1, min(2000, int(limit or 200)))
                    results = await music_manager.search_library_for_ui(query, limit=safe_limit)
                    api_elapsed = time.time() * 1000 - api_start_ms
                    logger.info(
                        f"🌐 Web API music_search_library (client {client_id}, limit={safe_limit}): "
                        f"{len(results)} rows in {api_elapsed:.1f}ms"
                    )
                    return results
                except Exception as exc:
                    elapsed = time.time() * 1000 - api_start_ms
                    logger.warning(f"Web UI music_search_library '{query}' after {elapsed:.1f}ms: {exc}")
                    return []

            async def _ui_music_list_playlists(client_id: str) -> list[str]:
                if not music_manager:
                    return []
                try:
                    return await music_manager.list_playlists()
                except Exception as exc:
                    logger.warning("Web UI music_list_playlists: %s", exc)
                    return []

            async def _ui_music_list_genres(limit: int, client_id: str) -> list[dict[str, Any]]:
                if not music_manager:
                    return []
                try:
                    safe_limit = max(1, min(100, int(limit or 100)))
                    return await music_manager.list_genres_for_ui(limit=safe_limit)
                except Exception as exc:
                    logger.warning("Web UI music_list_genres: %s", exc)
                    return []

            def _push_schedule_state_now(reason: str = "") -> None:
                if not web_service or not timer_manager:
                    return
                try:
                    timers = timer_manager.list_ui_timers()
                    alarms = alarm_manager.list_ui_alarms() if alarm_manager else []
                    entries: list[dict[str, Any]] = []
                    for t in timers:
                        item = dict(t)
                        item.setdefault("kind", "timer")
                        entries.append(item)
                    for a in alarms:
                        entries.append(dict(a))
                    entries.sort(
                        key=lambda x: (
                            0 if str(x.get("kind", "timer")).lower() == "alarm" else 1,
                            float(x.get("remaining_seconds", 0.0)),
                        )
                    )
                    web_service.update_timers_state(entries)
                    if reason:
                        logger.info("🕒 Pushed immediate schedule state (%s): %d entries", reason, len(entries))
                except Exception as exc:
                    logger.debug("Immediate schedule state push failed (%s): %s", reason, exc)

            async def _ui_timer_cancel(timer_id: str, client_id: str) -> None:
                if timer_manager:
                    try:
                        await timer_manager.cancel_timer(timer_id)
                        _push_schedule_state_now("timer_cancel")
                    except Exception as exc:
                        logger.warning("Web UI timer_cancel id=%s: %s", timer_id, exc)

            async def _ui_alarm_cancel(alarm_id: str, client_id: str) -> None:
                if alarm_manager:
                    try:
                        await alarm_manager.cancel_alarm(alarm_id)
                        _push_schedule_state_now("alarm_cancel")
                    except Exception as exc:
                        logger.warning("Web UI alarm_cancel id=%s: %s", alarm_id, exc)

            async def _ui_chat_new(client_id: str) -> None:
                await _start_new_session(source="web_ui", client_id=client_id)

            async def _ui_chat_text(text: str, client_id: str) -> None:
                nonlocal pending_transcripts, debounce_task

                # Intercept /reasoning directive before it reaches the transcript queue.
                reasoning_match = re.match(
                    r"^/reason(?:ing)?\s+(on|off|stream)\s*$", text.strip(), re.IGNORECASE
                )
                if reasoning_match:
                    level = reasoning_match.group(1).lower()
                    session_key = f"agent:{agent_id}:{session_id}"
                    logger.info("💬 /reasoning directive from %s: level=%s session=%s", client_id, level, session_key)
                    ack_text = f"Reasoning {level.upper()}"
                    try:
                        if hasattr(gateway, "patch_session"):
                            patch_value = None if level == "off" else level
                            await gateway.patch_session(session_key, reasoningLevel=patch_value)
                            ack_text = f"Reasoning {level.upper()} ✓"
                    except Exception as exc:
                        logger.warning("Failed to patch session reasoning level: %s", exc)
                        ack_text = f"Reasoning {level.upper()} (patch failed: {exc})"
                    if web_service:
                        try:
                            web_service.append_chat_message({"role": "assistant", "text": ack_text})
                        except Exception as exc:
                            logger.warning("Failed to append reasoning ack message: %s", exc)
                    return

                normalized = normalize_transcript(text)
                if not enqueue_pending_transcript(normalized, ""):
                    return
                logger.info("💬 Web UI text message from %s: '%s'", client_id, normalized[:120])
                if debounce_task and not debounce_task.done():
                    debounce_task.cancel()
                debounce_task = asyncio.create_task(send_debounced_transcripts(immediate=True))

            async def _ui_recordings_list(client_id: str) -> list[dict[str, Any]]:
                if not recordings_catalog:
                    return []
                return recordings_catalog.list_recordings()

            async def _ui_recording_get(recording_id: str, client_id: str) -> dict[str, Any] | None:
                if not recordings_catalog:
                    return None
                return recordings_catalog.get_recording_detail(recording_id)

            async def _ui_recordings_delete_selected(recording_ids: list[str], client_id: str) -> int:
                if not recordings_catalog:
                    return 0
                return await recordings_catalog.delete_recordings(recording_ids)

            async def _ui_recorder_start(client_id: str) -> dict[str, Any]:
                if not recorder_tool:
                    return {"success": False, "response": "Recording is not enabled on this device."}
                return await recorder_tool.start_recording()

            async def _ui_recorder_stop(client_id: str) -> dict[str, Any]:
                if not recorder_tool:
                    return {"success": False, "response": "Recording is not enabled on this device."}
                result = await recorder_tool.stop_recording(reason="ui")
                return {"success": True, "response": result.response}

            web_service.set_action_handlers(
                on_mic_toggle=_ui_mic_toggle,
                on_music_toggle=_ui_music_toggle,
                on_music_stop=_ui_music_stop,
                on_music_play_track=_ui_music_play_track,
                on_music_seek=_ui_music_seek,
                on_music_clear_queue=_ui_music_clear_queue,
                on_music_remove_selected=_ui_music_remove_selected,
                on_music_add_files=_ui_music_add_files,
                on_music_create_playlist=_ui_music_create_playlist,
                on_music_load_playlist=_ui_music_load_playlist,
                on_music_save_playlist=_ui_music_save_playlist,
                on_music_save_queue_then_clear_queue=_ui_music_save_queue_then_clear_queue,
                on_music_save_queue_then_load_playlist=_ui_music_save_queue_then_load_playlist,
                on_music_rename_playlist=_ui_music_rename_playlist,
                on_music_delete_playlist=_ui_music_delete_playlist,
                on_music_search_library=_ui_music_search_library,
                on_music_list_playlists=_ui_music_list_playlists,
                on_music_list_genres=_ui_music_list_genres,
                on_get_music_state=_ui_get_music_state_snapshot,
                on_recordings_list=_ui_recordings_list,
                on_recording_get=_ui_recording_get,
                on_recordings_delete_selected=_ui_recordings_delete_selected,
                on_recorder_start=_ui_recorder_start,
                on_recorder_stop=_ui_recorder_stop,
                on_resolve_recording_audio=recordings_catalog.resolve_audio_path,
                on_timer_cancel=_ui_timer_cancel,
                on_alarm_cancel=_ui_alarm_cancel,
                on_chat_new=_ui_chat_new,
                on_chat_text=_ui_chat_text,
                on_tts_mute_set=_ui_tts_mute_set,
                on_browser_audio_set=_ui_browser_audio_set,
                on_continuous_mode_set=_ui_continuous_mode_set,
            )

            _sync_music_output_route("post_web_handler_init")

            # If a browser connected before handlers were wired, push initial music data now.
            try:
                if music_manager:
                    playlist_names = await music_manager.list_playlists()
                    web_service.update_music_playlists(playlist_names or [])
                    transport, queue = await _ui_get_music_state_snapshot()
                    await web_service.push_music_state_now(queue=queue, **transport)
            except Exception as exc:
                logger.warning("Web UI post-handler initial sync failed: %s", exc)

            # Start music + timer state publisher
            async def _web_ui_publisher() -> None:
                last_music_transport_hash = ""
                last_music_queue_hash = ""
                last_music_queue_meta_hash = ""
                last_schedule_hash = ""
                last_schedule_shape_hash = ""
                music_failures = 0
                timer_failures = 0
                music_poll = config.web_ui_music_poll_ms / 1000.0
                music_queue_poll = max(5.0, music_poll * 10.0)
                timer_poll = config.web_ui_timer_poll_ms / 1000.0
                music_tick = 0.0
                music_queue_tick = 0.0
                music_queue_failures = 0
                music_queue_retry_after = 0.0
                music_transport_dirty = True
                music_queue_dirty = True
                timer_tick = 0.0
                music_queue_task: asyncio.Task | None = None
                music_queue_pending_reason = ""
                music_queue_pending_timeout_override: float | None = None

                async def _refresh_music_queue(reason: str, timeout_override: float | None = None) -> None:
                    nonlocal last_music_queue_hash, music_queue_tick, music_queue_failures, music_queue_retry_after
                    try:
                        base_timeout = min(2.5, max(1.0, float(config.music_command_timeout_s)))
                        q_timeout = float(timeout_override) if timeout_override is not None else base_timeout
                        q = await music_manager.get_ui_playlist(timeout=q_timeout)
                        qh = str([
                            (
                                item.get("id", ""),
                                item.get("pos", -1),
                                item.get("file", ""),
                                item.get("title", ""),
                                item.get("artist", ""),
                                item.get("album", ""),
                            )
                            for item in q
                        ])
                        if qh != last_music_queue_hash:
                            last_music_queue_hash = qh
                            web_service.update_music_queue(q)
                        music_queue_tick = time.monotonic()
                        music_queue_failures = 0
                        music_queue_retry_after = 0.0
                    except Exception as exc:
                        music_queue_failures += 1
                        backoff_s = min(60.0, max(5.0, float(2 ** min(music_queue_failures, 5))))
                        music_queue_retry_after = time.monotonic() + backoff_s
                        if music_queue_failures <= 3 or music_queue_failures % 10 == 0:
                            logger.warning(
                                "Web UI music queue refresh failed (%s) x%d; backing off %.1fs: %s",
                                reason,
                                music_queue_failures,
                                backoff_s,
                                exc,
                            )
                        music_queue_tick = time.monotonic()  # Back off — prevents tight retry loop on slow playlists

                def _schedule_music_queue_refresh(reason: str, timeout_override: float | None = None) -> None:
                    nonlocal music_queue_task, music_queue_pending_reason, music_queue_pending_timeout_override
                    if music_queue_task is not None and not music_queue_task.done():
                        # Keep one follow-up refresh queued so a post-load urgent refresh
                        # is not dropped behind an older in-flight queue fetch.
                        if timeout_override is not None:
                            music_queue_pending_reason = reason
                            pending_timeout = music_queue_pending_timeout_override
                            if pending_timeout is None or float(timeout_override) > float(pending_timeout):
                                music_queue_pending_timeout_override = float(timeout_override)
                        elif not music_queue_pending_reason:
                            music_queue_pending_reason = reason
                        return

                    async def _runner() -> None:
                        nonlocal music_queue_task, music_queue_pending_reason, music_queue_pending_timeout_override
                        try:
                            await _refresh_music_queue(reason, timeout_override=timeout_override)
                        finally:
                            music_queue_task = None
                            if music_queue_pending_reason:
                                next_reason = music_queue_pending_reason
                                next_timeout_override = music_queue_pending_timeout_override
                                music_queue_pending_reason = ""
                                music_queue_pending_timeout_override = None
                                _schedule_music_queue_refresh(next_reason, timeout_override=next_timeout_override)

                    music_queue_task = asyncio.create_task(_runner())

                try:
                    while True:
                        await asyncio.sleep(0.2)
                        if not web_service:
                            break
                        now = time.monotonic()
                        if timer_manager:
                            try:
                                timers = timer_manager.list_ui_timers()
                                alarms = alarm_manager.list_ui_alarms() if alarm_manager else []
                                entries = []
                                for t in timers:
                                    item = dict(t)
                                    item.setdefault("kind", "timer")
                                    entries.append(item)
                                for a in alarms:
                                    entries.append(dict(a))
                                entries.sort(key=lambda x: (0 if str(x.get("kind", "timer")).lower() == "alarm" else 1, float(x.get("remaining_seconds", 0.0))))
                                sh = str([
                                    (
                                        e.get("kind", "timer"),
                                        e.get("id"),
                                        int(round(float(e.get("remaining_seconds", 0.0)))),
                                        bool(e.get("ringing", False)),
                                    )
                                    for e in entries
                                ])
                                shape_hash = str([
                                    (
                                        e.get("kind", "timer"),
                                        e.get("id"),
                                        bool(e.get("ringing", False)),
                                        bool(e.get("enabled", True)),
                                        bool(e.get("triggered", False)),
                                    )
                                    for e in entries
                                ])
                                immediate_shape_change = shape_hash != last_schedule_shape_hash
                                periodic_refresh_due = (now - timer_tick) >= timer_poll
                                if immediate_shape_change or (periodic_refresh_due and sh != last_schedule_hash):
                                    last_schedule_hash = sh
                                    last_schedule_shape_hash = shape_hash
                                    timer_tick = now
                                    web_service.update_timers_state(entries)
                                timer_failures = 0
                            except Exception as exc:
                                timer_failures += 1
                                if timer_failures <= 3 or timer_failures % 20 == 0:
                                    logger.warning(
                                        "Web UI timer publisher failed (%d): %s",
                                        timer_failures,
                                        exc,
                                    )
                        if music_manager and (music_transport_dirty or (now - music_tick) >= music_poll):
                            music_tick = now
                            try:
                                music_timeout_s = max(0.2, min(1.0, music_poll))
                                ms = await asyncio.wait_for(music_manager.get_ui_music_state(), timeout=music_timeout_s)
                                th = str(sorted(ms.items()))
                                if th != last_music_transport_hash:
                                    last_music_transport_hash = th
                                    web_service.update_music_transport(**ms)
                                queue_meta_hash = str((
                                    ms.get("queue_length", 0),
                                    ms.get("loaded_playlist", ""),
                                    ms.get("playlist_version", ""),
                                ))
                                queue_poll_due = (now - music_queue_tick) >= music_queue_poll
                                # Fast path: a music_load_playlist action just completed.
                                # Reset any backoff and kick an immediate queue refresh with
                                # a more generous timeout for freshly-loaded large playlists.
                                if _post_load_queue_event.is_set():
                                    _post_load_queue_event.clear()
                                    music_queue_failures = 0
                                    music_queue_retry_after = 0.0
                                    music_queue_dirty = True
                                    _schedule_music_queue_refresh("post_load_urgent", timeout_override=5.0)
                                    last_music_queue_meta_hash = queue_meta_hash
                                can_refresh_queue = now >= music_queue_retry_after
                                if can_refresh_queue and (music_queue_dirty or queue_meta_hash != last_music_queue_meta_hash or queue_poll_due):
                                    last_music_queue_meta_hash = queue_meta_hash
                                    _schedule_music_queue_refresh("publisher")
                                    music_queue_dirty = False
                                music_transport_dirty = False
                                music_failures = 0
                            except asyncio.TimeoutError:
                                music_failures += 1
                                if music_failures <= 3 or music_failures % 20 == 0:
                                    logger.warning(
                                        "Web UI music publisher timed out (%d) after %.0fms",
                                        music_failures,
                                        music_timeout_s * 1000.0,
                                    )
                            except Exception as exc:
                                music_failures += 1
                                if music_failures <= 3 or music_failures % 20 == 0:
                                    logger.warning(
                                        "Web UI music publisher failed (%d): %s",
                                        music_failures,
                                        exc,
                                    )
                finally:
                    pass

            asyncio.create_task(_web_ui_publisher())

        except Exception as exc:
            logger.error("Failed to start embedded web UI: %s", exc)
            print(f"✗ Embedded Voice Web UI failed to start: {exc}", flush=True)
            web_service = None

    def _tts_has_pending() -> bool:
        return len(tts_queue) > 0

    def _tts_drop_stale_replies(new_request_id: int) -> int:
        if new_request_id <= 0 or not tts_queue:
            return 0
        original_len = len(tts_queue)
        kept = deque(
            item
            for item in tts_queue
            if not (item.kind == "reply" and item.request_id < new_request_id)
        )
        dropped = original_len - len(kept)
        if dropped:
            tts_queue.clear()
            tts_queue.extend(kept)
        return dropped

    def _tts_clear_all(reason: str) -> int:
        dropped = len(tts_queue)
        if dropped:
            tts_queue.clear()
            logger.info("🧹 Cleared %d queued TTS item(s): %s", dropped, reason)
        tts_queue_event.clear()
        return dropped

    async def stop_ringing_alarms_immediately(source_label: str) -> int:
        """Stop all actively ringing alarms and interrupt current alarm bell playback."""
        # Shared stop signal also interrupts in-flight timer bell playback.
        alarm_playback_stop_event.set()
        stopped_count = 0
        try:
            if alarm_manager:
                stopped_count = await alarm_manager.stop_alarm(None)
            # Keep stop signal asserted briefly so in-flight playback writes observe it.
            await asyncio.sleep(0.12)
        finally:
            # Allow future alarms to ring after the current dismiss action.
            alarm_playback_stop_event.clear()

        if stopped_count > 0:
            logger.info(
                "🔕 %s stopped %d ringing alarm%s",
                source_label,
                stopped_count,
                "s" if stopped_count != 1 else "",
            )
        return stopped_count

    MAX_NOTIFICATION_QUEUE_DEPTH = 5

    def _tts_enqueue(item: TTSQueueItem) -> None:
        if item.kind == "notification":
            notification_count = sum(1 for i in tts_queue if i.kind == "notification")
            if notification_count >= MAX_NOTIFICATION_QUEUE_DEPTH:
                # Drop oldest notification to make room
                for idx, queued in enumerate(tts_queue):
                    if queued.kind == "notification":
                        del tts_queue[idx]
                        logger.warning(
                            "⚠️ Notification queue full (%d); dropped oldest: %.40s…",
                            MAX_NOTIFICATION_QUEUE_DEPTH,
                            queued.text,
                        )
                        break
        tts_queue.append(item)
        tts_queue_event.set()

    async def _tts_dequeue() -> TTSQueueItem:
        while True:
            if tts_queue:
                item = tts_queue.popleft()
                if not tts_queue:
                    tts_queue_event.clear()
                return item
            tts_queue_event.clear()
            await tts_queue_event.wait()

    def _strip_markdown_for_tts_word_count(text: str) -> str:
        """Normalize markdown-heavy responses for spoken-word counting."""
        normalized = str(text or "")
        normalized = re.sub(r"```[\s\S]*?```", " ", normalized)
        normalized = re.sub(r"`[^`]*`", " ", normalized)
        normalized = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r" \1 ", normalized)
        normalized = re.sub(r"(?m)^\s{0,3}[>#*-]+\s*", "", normalized)
        normalized = re.sub(r"[_*~]+", " ", normalized)
        return normalized

    def _count_spoken_words_for_tts(text: str) -> int:
        """Count words while ignoring punctuation-heavy/code-only content."""
        normalized = _strip_markdown_for_tts_word_count(text)
        tokens = re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", normalized)
        return len(tokens)

    def clean_text_for_tts(text: str) -> str:
        """Remove punctuation and icon symbols that should not be spoken by TTS.
        
        Removes: colons, semicolons, quotes, brackets, parentheses
        Removes: emoji/icon symbol characters
        Keeps: periods, commas, dashes (natural for pacing/reading)
        """
        def _url_to_domain(raw_url: str) -> str:
            candidate = raw_url.strip().strip(".,!?;:)")
            if not candidate:
                return raw_url

            parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
            domain = parsed.netloc.lower().strip()
            if "@" in domain:
                domain = domain.split("@", 1)[-1]
            if ":" in domain:
                domain = domain.split(":", 1)[0]
            return domain or raw_url

        def _spaced_url_to_domain(raw_url: str) -> str:
            compact = re.sub(r"\s*([:/?#=&])\s*", r"\1", raw_url)
            compact = re.sub(r"\s+", "", compact)
            return _url_to_domain(compact)

        # Convert full links to domains only for speech.
        text = re.sub(
            r"\bhttps?\s*:\s*/\s*/\s*[^\s<>\"']+(?:\s*/\s*[^\s<>\"']*)*",
            lambda m: _spaced_url_to_domain(m.group(0)),
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"https?://[^\s<>\"']+", lambda m: _url_to_domain(m.group(0)), text, flags=re.IGNORECASE)
        text = re.sub(r"\bwww\.[^\s<>\"']+", lambda m: _url_to_domain(m.group(0)), text, flags=re.IGNORECASE)

        # Remove punctuation that would be read aloud
        # Keep colons in time expressions (e.g., 9:45 PM), remove other colons.
        text = re.sub(r"(?<!\d):(?!\d)", "", text)
        text = text.replace(";", "")  # semicolon
        text = text.replace('"', "")  # quote
        text = text.replace("(", "")  # open paren
        text = text.replace(")", "")  # close paren
        text = text.replace("[", "")  # open bracket
        text = text.replace("]", "")  # close bracket
        text = text.replace("{", "")  # open brace
        text = text.replace("}", "")  # close brace
        text = text.replace("/", " ")  # slash -> space

        # Preserve contraction apostrophes (I'm, don't) but normalize curly to straight.
        text = text.replace("’", "'")

        # Remove emoji/icon symbols and emoji formatting code points.
        # So = Symbol, Other (emoji/pictographs), plus variation selector/joiner helpers.
        text = "".join(
            ch
            for ch in text
            if unicodedata.category(ch) != "So" and ch not in {"\ufe0f", "\u200d"}
        )

        # Expand negative numbers so TTS reads them correctly (e.g. -4 → minus 4).
        text = re.sub(r'(?<!\w)-(\d)', r'minus \1', text)

        # Clean up multiple spaces
        text = re.sub(r'\s+', ' ', text).strip()

        # If nothing speakable remains, skip TTS for this chunk.
        if not any(ch.isalnum() for ch in text):
            return ""

        return text

    async def submit_tts(
        text: str,
        request_id: int = 0,
        kind: str = "reply",
        allow_when_ui_tts_muted: bool = False,
    ) -> None:
        nonlocal last_tts_text, last_tts_ts
        nonlocal current_tts_text, current_tts_duration_s, tts_playback_start_ts
        nonlocal tts_playing, current_tts_request_id
        nonlocal cut_in_tts_hold_active, cut_in_tts_hold_started_ts, cut_in_tts_hold_request_id

        text = strip_gateway_control_markers(text).strip()
        if not text:
            logger.info("🚫 Filtered gateway control marker-only TTS payload")
            return
        
        # Filter out NO_REPLY markers (final safeguard)
        if "NO_REPLY" in text or "NO_RE" in text or text.strip() in ["NO", "_RE", "NO _RE"]:
            logger.info("🚫 Filtered NO_REPLY from TTS: '%s'", text)
            return

        normalized_kind = "notification" if kind == "notification" else "reply"
        effective_request_id = request_id if request_id else current_request_id
        if normalized_kind == "reply" and effective_request_id <= 0:
            effective_request_id = current_request_id
        if normalized_kind == "reply" and effective_request_id < current_request_id:
            logger.info(
                "🚫 Dropped stale reply TTS [req#%d < current#%d]",
                effective_request_id,
                current_request_id,
            )
            return
        
        now = time.monotonic()
        if text == last_tts_text and (now - last_tts_ts) * 1000 < tts_dedupe_window_ms:
            return

        # Only interrupt if this is a new request (different from currently playing)
        if request_id and request_id != current_tts_request_id and tts_playing and current_tts_text and tts_playback_start_ts is not None:
            logger.info("🔄 TTS [req#%d→%d]: New user message arrived; stopping current playback", current_tts_request_id, request_id)
            tts_stop_event.set()
        elif tts_playing and current_tts_text and tts_playback_start_ts is not None and (not request_id or request_id == current_tts_request_id):
            # Same request, so strip prefix to avoid re-speaking already played content
            elapsed = now - tts_playback_start_ts
            trimmed = strip_spoken_prefix(text, current_tts_text, elapsed, current_tts_duration_s)
            if trimmed != text:
                logger.info("✂️ TTS [req#%d]: Stripped spoken prefix (%d→%d chars)", request_id, len(text), len(trimmed))
            text = trimmed

        if not text:
            return

        # Clean punctuation that would be spoken as words
        text = clean_text_for_tts(text)
        if not text:
            logger.info("🚫 Text became empty after punctuation/icon cleanup")
            return

        last_tts_text = text
        last_tts_ts = now
        if normalized_kind == "reply":
            dropped = _tts_drop_stale_replies(effective_request_id)
            if dropped:
                logger.info("🧹 Dropped %d stale reply TTS item(s) before enqueue [req#%d]", dropped, effective_request_id)

        _tts_enqueue(
            TTSQueueItem(
                text=text,
                request_id=effective_request_id,
                kind=normalized_kind,
                allow_when_ui_tts_muted=bool(allow_when_ui_tts_muted),
                created_ts=now,
            )
        )

    def _active_gateway_collation_request_id() -> int:
        if gateway_collation_open and gateway_collation_active_request_id > 0:
            return gateway_collation_active_request_id
        return 0

    def _force_close_gateway_collation_window(reason: str) -> None:
        nonlocal gateway_collation_open, gateway_collation_active_request_id
        nonlocal gateway_collation_last_frame_ts, gateway_collation_close_task
        if gateway_collation_close_task and not gateway_collation_close_task.done():
            gateway_collation_close_task.cancel()
        if gateway_collation_open or gateway_collation_active_request_id:
            logger.info(
                "🧩 Force-closed gateway collation window [req#%d] (%s)",
                gateway_collation_active_request_id,
                reason,
            )
        gateway_collation_open = False
        gateway_collation_active_request_id = 0
        gateway_collation_last_frame_ts = None

    def _open_gateway_collation_window(request_id: int) -> None:
        nonlocal gateway_collation_open, gateway_collation_active_request_id
        nonlocal gateway_collation_last_frame_ts, gateway_collation_close_task
        if request_id <= 0:
            return
        if gateway_collation_close_task and not gateway_collation_close_task.done():
            gateway_collation_close_task.cancel()
        gateway_collation_open = True
        gateway_collation_active_request_id = int(request_id)
        gateway_collation_last_frame_ts = time.monotonic()
        logger.info("🧩 Opened gateway collation window [req#%d]", request_id)

    def _touch_gateway_collation_window(request_id: int) -> bool:
        nonlocal gateway_collation_last_frame_ts
        if request_id <= 0:
            return False
        if not gateway_collation_open:
            return False
        if gateway_collation_active_request_id != int(request_id):
            return False
        gateway_collation_last_frame_ts = time.monotonic()
        return True

    def _schedule_gateway_collation_close(request_id: int, reason: str, delay_s: float = 1.5) -> None:
        nonlocal gateway_collation_close_task
        if request_id <= 0:
            return
        if gateway_collation_close_task and not gateway_collation_close_task.done():
            gateway_collation_close_task.cancel()

        async def _close_later() -> None:
            nonlocal gateway_collation_open, gateway_collation_active_request_id, gateway_collation_last_frame_ts
            try:
                await asyncio.sleep(max(0.0, float(delay_s)))
            except asyncio.CancelledError:
                return
            if gateway_collation_open and gateway_collation_active_request_id == int(request_id):
                gateway_collation_open = False
                gateway_collation_active_request_id = 0
                gateway_collation_last_frame_ts = None
                logger.info("🧩 Closed gateway collation window [req#%d] (%s)", request_id, reason)

        gateway_collation_close_task = asyncio.create_task(_close_later())

    def _append_recorder_finished_chat(stop_result: Any) -> None:
        if not web_service:
            return
        if not stop_result or not getattr(stop_result, "audio_path", ""):
            return
        try:
            duration_seconds = float(getattr(stop_result, "duration_seconds", 0.0) or 0.0)
        except Exception:
            duration_seconds = 0.0
        rounded = max(0, int(round(duration_seconds)))
        minutes = rounded // 60
        seconds = rounded % 60
        if minutes > 0:
            duration_phrase = f"{minutes} minute" + ("s" if minutes != 1 else "")
            if seconds > 0:
                duration_phrase += f" {seconds} second" + ("s" if seconds != 1 else "")
        else:
            duration_phrase = f"{seconds} second" + ("s" if seconds != 1 else "")
        try:
            web_service.append_chat_message(
                {
                    "role": "assistant",
                    "source": "recorder",
                    "text": f"Finished recording {duration_phrase} of audio",
                }
            )
        except Exception as exc:
            logger.warning("Failed to append recorder completion chat message: %s", exc)

    def _safe_append_chat_message(message: dict[str, Any], context: str) -> None:
        if not web_service:
            return
        try:
            web_service.append_chat_message(message)
        except Exception as exc:
            logger.warning("Failed to append web chat message (%s): %s", context, exc)

    def _safe_update_or_append_chat_message(message: dict[str, Any], context: str) -> None:
        """Append or update user message if it's a prefix extension of the last one."""
        if not web_service:
            return
        try:
            web_service.update_or_append_chat_message(message)
        except Exception as exc:
            logger.warning("Failed to update or append web chat message (%s): %s", context, exc)

    def _collect_summary_user_prompt(current_user_text: str) -> str:
        """Collect the latest contiguous block of user turns for summary generation."""
        current_norm = normalize_transcript(current_user_text)
        prompts: list[str] = []

        if web_service:
            try:
                history = list(getattr(web_service, "_chat_messages", []) or [])
            except Exception:
                history = []

            for msg in reversed(history):
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role") or "").strip().lower()
                seg_kind = str(msg.get("segment_kind") or "").strip().lower()

                # Ignore non-conversational frames and in-flight stream deltas.
                if role in {"raw_gateway", "step", "interim", "context_group", "gateway_debug_group"}:
                    continue
                if role == "assistant" and seg_kind == "stream":
                    continue

                if role == "assistant":
                    break

                if role == "user":
                    txt = normalize_transcript(str(msg.get("text") or ""))
                    if txt:
                        prompts.append(txt)
                    continue

                if prompts:
                    break

        prompts.reverse()

        if current_norm:
            if not prompts or normalize_transcript(prompts[-1]) != current_norm:
                prompts.append(current_norm)

        deduped: list[str] = []
        for txt in prompts:
            if not deduped or normalize_transcript(deduped[-1]) != normalize_transcript(txt):
                deduped.append(txt)

        return "\n".join(deduped)

    def _latest_stream_text_for_request(request_id: int) -> str:
        """Return the latest in-progress streamed assistant text for a request."""
        if not web_service:
            return ""
        try:
            history = list(getattr(web_service, "_chat_messages", []) or [])
        except Exception:
            return ""

        req_key = str(request_id)
        for msg in reversed(history):
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "").strip().lower() != "assistant":
                continue
            if str(msg.get("segment_kind") or "").strip().lower() != "stream":
                continue
            if str(msg.get("request_id") or "") != req_key:
                continue
            return str(msg.get("text") or "").strip()
        return ""

    def _summary_looks_like_response_excerpt(summary_text: str, full_response_text: str) -> bool:
        """Detect summaries that mostly mirror generated output text."""
        summary_norm = re.sub(r"\s+", " ", str(summary_text or "")).strip().lower()
        full_norm = re.sub(r"\s+", " ", str(full_response_text or "")).strip().lower()
        if not summary_norm or not full_norm:
            return False
        if summary_norm in full_norm:
            return True
        summary_tokens = summary_norm.split()
        full_tokens = full_norm.split()
        if len(summary_tokens) >= 6 and len(full_tokens) >= 6 and summary_tokens[:6] == full_tokens[:6]:
            return True
        return False

    def _summary_topic_hint(user_prompt: str, max_words: int = 10) -> str:
        """Extract a short topic hint from the latest user turn for fallback summaries."""
        lines = [normalize_transcript(line) for line in str(user_prompt or "").splitlines()]
        nonempty = [line for line in lines if line]
        latest = nonempty[-1] if nonempty else ""
        if not latest:
            return ""
        words = latest.split()
        hint = " ".join(words[:max_words]).strip()
        if len(words) > max_words:
            hint = hint.rstrip(" ,;:") + "…"
        return hint

    async def send_debounced_transcripts(immediate: bool = False) -> None:
        """Send accumulated transcripts after debounce period."""
        nonlocal pending_transcripts, processing_request, debounce_task
        nonlocal music_paused_for_wake, music_auto_resume_timer
        nonlocal last_user_text, last_user_accepted_ts, last_user_went_upstream
        nonlocal last_assistant_text, last_assistant_source, last_assistant_ts
        nonlocal last_assistant_was_question, last_assistant_expects_short_reply
        nonlocal last_upstream_assistant_text, last_upstream_assistant_ts
        nonlocal last_upstream_response_was_question, last_upstream_response_requested_confirmation
        nonlocal suppress_gateway_messages_for_new_session
        
        # If already processing, let that task handle everything
        if processing_request:
            logger.info("⏱️ Debounce timer skipped - already processing a request")
            return
        
        if immediate:
            logger.info("⏱️ Immediate dispatch requested (web text input)")
        else:
            logger.info("⏱️ Debounce timer started (will fire in %dms)", config.gateway_debounce_ms)
            await asyncio.sleep(config.gateway_debounce_ms / 1000)
        
        logger.info("⏱️ Debounce timer fired with %d pending transcripts", len(pending_transcripts))
        if not pending_transcripts:
            logger.info("⏱️ No transcripts to send (pending_transcripts is empty)")
            return
        
        # Set flag to prevent concurrent processing
        processing_request = True
        
        try:
            # Save current transcripts but don't clear yet (more may arrive during LLM call)
            initial_transcripts = list(pending_transcripts)
            initial_count = len(initial_transcripts)

            def _merge_incremental_transcript_parts(parts: list[str]) -> str:
                merged = ""

                def _token_overlap_words(left: str, right: str) -> int:
                    left_words = left.split()
                    right_words = right.split()
                    max_overlap = min(len(left_words), len(right_words))
                    for overlap in range(max_overlap, 0, -1):
                        if [w.lower() for w in left_words[-overlap:]] == [w.lower() for w in right_words[:overlap]]:
                            return overlap
                    return 0

                for raw_part in parts:
                    part = normalize_transcript(raw_part)
                    if not part:
                        continue
                    if not merged:
                        merged = part
                        continue

                    merged_lower = merged.lower()
                    part_lower = part.lower()

                    if part_lower == merged_lower:
                        continue
                    if part_lower.startswith(merged_lower):
                        merged = part
                        continue
                    if merged_lower.startswith(part_lower):
                        continue

                    overlap_words = _token_overlap_words(merged, part)
                    if overlap_words > 0:
                        part_words = part.split()
                        merged = f"{merged} {' '.join(part_words[overlap_words:])}".strip()
                    else:
                        merged = f"{merged} {part}".strip()

                return merged
            
            # Combine initial transcripts
            combined_transcript = _merge_incremental_transcript_parts([t[0] for t in initial_transcripts])
            # Use emotion from first transcript (or combine if needed)
            emotion_tag = initial_transcripts[0][1] if initial_transcripts else ""

            combined_transcript = normalize_transcript(combined_transcript)
            if not combined_transcript:
                logger.info("⊘ Debounced transcript became empty after normalization; skipping quick answer and gateway")
                pending_transcripts.clear()
                return

            parsed_music: tuple[str, dict[str, Any] | str] | None = None
            canonical_combined = canonicalize_transcript_for_match(combined_transcript)
            stop_transcript_intent = canonical_combined in {"stop transcript", "stop transcription"}

            # Belt-and-suspenders: if user explicitly asked to stop/pause music,
            # clear wake-pause auto-resume state immediately so music cannot
            # restart on the silence timer path.
            if config.music_enabled and music_router:
                try:
                    parsed_music = music_router.parser.parse(combined_transcript)
                except Exception:
                    parsed_music = None
                if parsed_music and parsed_music[0] == "stop":
                    if music_paused_for_wake or music_auto_resume_timer != 0.0:
                        logger.info("🎵 Explicit stop intent detected → clearing wake pause/auto-resume state")
                    music_paused_for_wake = False
                    music_auto_resume_timer = 0.0

                    if stop_transcript_intent:
                        dismissed_alert_alarms = await stop_ringing_alarms_immediately("voice stop transcript")
                        # Interrupt any in-flight timer/alarm notification speech too.
                        tts_stop_event.set()
                        dropped_alert_tts = _tts_clear_all("voice stop transcript silenced alerts")
                        if dismissed_alert_alarms > 0 or dropped_alert_tts > 0:
                            logger.info(
                                "🔕 Voice stop transcript silenced alerts (alarms_stopped=%d, tts_cleared=%d)",
                                dismissed_alert_alarms,
                                dropped_alert_tts,
                            )
            
            # Increment request ID for new user message
            nonlocal current_request_id, startup_phase_active
            current_request_id += 1
            _force_close_gateway_collation_window("new user message")
            logger.info("📍 New user message [req#%d]", current_request_id)
            print(f"\033[93m→ USER: {combined_transcript}\033[0m", flush=True)
            is_music_query = bool(music_router and music_router.is_music_related(combined_transcript))
            _safe_update_or_append_chat_message(
                {
                    "role": "user",
                    "text": combined_transcript,
                    "source": "voice",
                    "request_id": current_request_id,
                },
                "voice_user",
            )

            last_user_text = combined_transcript
            last_user_accepted_ts = time.monotonic()
            last_user_went_upstream = False
            
            # Try quick answer first if enabled
            should_send_to_gateway = True
            quick_answer_model_recommendation: dict[str, str] | None = None

            # Hard local fast-path for timers/alarms before any QA bypass logic.
            # This guarantees deterministic timer handling stays local even if quick-answer
            # is unavailable or currently in a bypass window.
            if timers_feature_enabled and tool_router:
                try:
                    direct_timer_result = await tool_router.try_deterministic_parse(combined_transcript)
                except Exception as exc:
                    logger.debug("Direct timer/alarm fast-path parse failed: %s", exc)
                    direct_timer_result = None

                if direct_timer_result is not None:
                    from orchestrator.gateway.quick_answer import sanitize_quick_answer_text

                    local_response = sanitize_quick_answer_text(direct_timer_result)
                    logger.info("✓ TIMER FAST-PATH: handled locally before QA/upstream")
                    startup_phase_active = False

                    if local_response:
                        print(f"\033[94m← TIMER FAST-PATH: {local_response}\033[0m", flush=True)
                        logger.info("→ TTS QUEUE [req#%d]: Enqueuing timer fast-path response: '%s'", current_request_id, local_response[:80])
                        last_activity_ts = time.monotonic()
                        await submit_tts(local_response, request_id=current_request_id)
                        _safe_append_chat_message(
                            {
                                "role": "assistant",
                                "text": local_response,
                                "tts_text": local_response,
                                "source": "timer_fast_path",
                                "request_id": current_request_id,
                                "segment_kind": "final",
                            },
                            "timer_fast_path_assistant",
                        )
                        last_assistant_text = local_response
                        last_assistant_source = "timer_fast_path"
                        last_assistant_ts = time.monotonic()
                        last_assistant_was_question = assistant_turn_is_question(local_response)
                        last_assistant_expects_short_reply = assistant_turn_expects_short_reply(local_response)

                    should_send_to_gateway = False
                    last_user_went_upstream = False

                    # Preserve any transcripts that arrived while we were handling the fast-path.
                    trailing_transcripts = pending_transcripts[initial_count:] if len(pending_transcripts) > initial_count else []
                    filtered_trailing: list[tuple[str, str]] = []
                    for trailing_text, trailing_emotion in trailing_transcripts:
                        trailing_norm = normalize_transcript(trailing_text)
                        if not trailing_norm:
                            continue
                        if _is_incremental_prefix_extension(combined_transcript, trailing_norm):
                            continue
                        if _is_incremental_prefix_extension(trailing_norm, combined_transcript):
                            continue
                        filtered_trailing.append((trailing_norm, trailing_emotion))

                    pending_transcripts = filtered_trailing
                    if pending_transcripts:
                        if debounce_task and not debounce_task.done():
                            debounce_task.cancel()
                        debounce_task = asyncio.create_task(send_debounced_transcripts())

            # Hard local fast-path for deterministic music commands before QA/upstream.
            # This prevents clear local music intents (e.g. "play some jazz music")
            # from leaking upstream when QA bypassing/escalation heuristics are active.
            if should_send_to_gateway and config.music_enabled and music_router:
                try:
                    direct_music_result = await music_router.handle_request(combined_transcript, use_fast_path=True)
                except Exception as exc:
                    logger.debug("Direct music fast-path execution failed: %s", exc)
                    direct_music_result = None

                # Music fast-path returns None when no deterministic command matched.
                if direct_music_result is not None:
                    from orchestrator.gateway.quick_answer import sanitize_quick_answer_text

                    local_response = sanitize_quick_answer_text(direct_music_result)
                    logger.info("✓ MUSIC FAST-PATH: handled locally before QA/upstream")
                    startup_phase_active = False

                    if local_response:
                        print(f"\033[94m← MUSIC FAST-PATH: {local_response}\033[0m", flush=True)
                        logger.info("→ TTS QUEUE [req#%d]: Enqueuing music fast-path response: '%s'", current_request_id, local_response[:80])
                        last_activity_ts = time.monotonic()
                        await submit_tts(local_response, request_id=current_request_id)
                        _safe_append_chat_message(
                            {
                                "role": "assistant",
                                "text": local_response,
                                "tts_text": local_response,
                                "source": "music_fast_path",
                                "request_id": current_request_id,
                                "segment_kind": "final",
                            },
                            "music_fast_path_assistant",
                        )
                        last_assistant_text = local_response
                        last_assistant_source = "music_fast_path"
                        last_assistant_ts = time.monotonic()
                        last_assistant_was_question = assistant_turn_is_question(local_response)
                        last_assistant_expects_short_reply = assistant_turn_expects_short_reply(local_response)
                    else:
                        logger.info("→ MUSIC FAST-PATH [req#%d]: Silent success (no TTS response)", current_request_id)

                    should_send_to_gateway = False
                    last_user_went_upstream = False

                    # Preserve any transcripts that arrived while we were handling the fast-path.
                    trailing_transcripts = pending_transcripts[initial_count:] if len(pending_transcripts) > initial_count else []
                    filtered_trailing: list[tuple[str, str]] = []
                    for trailing_text, trailing_emotion in trailing_transcripts:
                        trailing_norm = normalize_transcript(trailing_text)
                        if not trailing_norm:
                            continue
                        if _is_incremental_prefix_extension(combined_transcript, trailing_norm):
                            continue
                        if _is_incremental_prefix_extension(trailing_norm, combined_transcript):
                            continue
                        filtered_trailing.append((trailing_norm, trailing_emotion))

                    pending_transcripts = filtered_trailing
                    if pending_transcripts:
                        if debounce_task and not debounce_task.done():
                            debounce_task.cancel()
                        debounce_task = asyncio.create_task(send_debounced_transcripts())

            nonlocal last_gateway_send_ts, last_thinking_phrase_ts
            bypass_window_ms = config.quick_answer_bypass_window_ms
            in_bypass_window = (
                bypass_window_ms > 0
                and last_gateway_send_ts is not None
                and (time.monotonic() - last_gateway_send_ts) * 1000 < bypass_window_ms
            )
            local_skill_query = False
            if quick_answer_client:
                try:
                    should_force_upstream_local, local_reason = classify_upstream_decision(
                        combined_transcript,
                        timers_enabled=quick_answer_client.timers_enabled,
                        music_enabled=quick_answer_client.music_enabled,
                        recorder_enabled=quick_answer_client.recorder_enabled,
                        new_session_enabled=quick_answer_client.new_session_enabled,
                    )
                    local_skill_query = (not should_force_upstream_local) and local_reason in {
                        "timer_alarm_local",
                        "music_local",
                        "recorder_local",
                        "new_session_local",
                        "date_time_local",
                    }
                except Exception:
                    local_skill_query = False
            if in_bypass_window and is_music_query:
                logger.info(
                    "⏩ QA bypass override: music command detected; running quick-answer/tool path despite %dms bypass window",
                    bypass_window_ms,
                )
                in_bypass_window = False
            if in_bypass_window and local_skill_query:
                logger.info(
                    "⏩ QA bypass override: local-skill intent detected; running quick-answer/tool path despite %dms bypass window",
                    bypass_window_ms,
                )
                in_bypass_window = False
            if in_bypass_window:
                logger.info(
                    "⏩ QA bypass: within %dms of last gateway send; skipping quick answer",
                    bypass_window_ms,
                )
            if quick_answer_client and not in_bypass_window:
                try:
                    qa_start = time.monotonic()
                    # Use tool-enabled method if tools are configured
                    if quick_answer_client.has_tool_capabilities():
                        should_use_upstream, quick_response = await quick_answer_client.get_quick_answer_with_tools(combined_transcript)
                    else:
                        should_use_upstream, quick_response = await quick_answer_client.get_quick_answer(combined_transcript)
                    qa_elapsed = int((time.monotonic() - qa_start) * 1000)
                    if should_use_upstream:
                        quick_answer_model_recommendation = quick_answer_client.pop_last_model_recommendation()
                    if not should_use_upstream:
                        # Quick answer handled locally. Some command classes intentionally
                        # return empty text to keep interactions silent (e.g. play/stop).
                        logger.info("✓ QUICK ANSWER: Using LLM/tool response instead of gateway (latency: %dms)", qa_elapsed)
                        startup_phase_active = False  # Assistant responded locally; clear startup phase
                        trailing_transcripts = pending_transcripts[initial_count:] if len(pending_transcripts) > initial_count else []
                        if quick_response:
                            print(f"\033[94m← QUICK ANSWER: {quick_response} [latency: {qa_elapsed}ms]\033[0m", flush=True)
                            logger.info("→ TTS QUEUE [req#%d]: Enqueuing quick answer: '%s'", current_request_id, quick_response[:80])
                            last_activity_ts = time.monotonic()
                            await submit_tts(quick_response, request_id=current_request_id)
                            if quick_response:
                                _safe_append_chat_message(
                                    {
                                        "role": "assistant",
                                        "text": quick_response,
                                        "tts_text": quick_response,
                                        "source": "quick_answer",
                                        "request_id": current_request_id,
                                        "segment_kind": "final",
                                    },
                                    "quick_answer_assistant",
                                )
                            last_assistant_text = quick_response
                            last_assistant_source = "quick_answer"
                            last_assistant_ts = time.monotonic()
                            last_assistant_was_question = assistant_turn_is_question(quick_response)
                            last_assistant_expects_short_reply = assistant_turn_expects_short_reply(quick_response)
                        else:
                            logger.info("→ QUICK ANSWER [req#%d]: Silent success (no TTS response)", current_request_id)

                        should_send_to_gateway = False
                        last_user_went_upstream = False
                        # Keep any transcripts that arrived during QA handling so they can be debounced
                        # into a follow-up request (often incremental STT extensions).
                        filtered_trailing: list[tuple[str, str]] = []
                        for trailing_text, trailing_emotion in trailing_transcripts:
                            trailing_norm = normalize_transcript(trailing_text)
                            if not trailing_norm:
                                continue
                            if _is_incremental_prefix_extension(combined_transcript, trailing_norm):
                                logger.info("⏱️ Dropping trailing transcript that extends already-handled text")
                                continue
                            if _is_incremental_prefix_extension(trailing_norm, combined_transcript):
                                logger.info("⏱️ Dropping trailing transcript that duplicates handled prefix")
                                continue
                            filtered_trailing.append((trailing_norm, trailing_emotion))

                        pending_transcripts = filtered_trailing
                        if pending_transcripts:
                            logger.info(
                                "⏱️ QUICK ANSWER local handled; preserving %d trailing transcript(s) for next debounce",
                                len(pending_transcripts),
                            )
                            if debounce_task and not debounce_task.done():
                                debounce_task.cancel()
                            debounce_task = asyncio.create_task(send_debounced_transcripts())
                        # Mirror both turns to the openclaw session so they appear in the web chat UI
                        if config.quick_answer_mirror_enabled and quick_response:
                            from orchestrator.gateway.providers import OpenClawGateway
                            if isinstance(gateway, OpenClawGateway):
                                mirror_session_key = f"agent:{agent_id}:{session_id}"
                                async def _mirror_qa(user_text: str, assistant_text: str, sk: str) -> None:
                                    try:
                                        await gateway.inject_message(sk, user_text, label="🎤 Voice")
                                        await gateway.inject_message(sk, assistant_text)
                                        logger.info("✓ QA MIRROR: Injected QA pair to session %s", sk)
                                    except Exception as mirror_exc:
                                        logger.warning("QA MIRROR: Failed to inject turns to %s: %s", sk, mirror_exc)
                                asyncio.create_task(_mirror_qa(combined_transcript, quick_response, mirror_session_key))
                    else:
                        # Need to escalate to gateway - check for additional transcripts
                        logger.info("← QUICK ANSWER: Escalating to upstream (latency: %dms)", qa_elapsed)
                        
                        # Play a thinking phrase while gateway processes (suppress if one was said recently)
                        thinking_suppress_ms = 12000
                        if last_thinking_phrase_ts is None or (time.monotonic() - last_thinking_phrase_ts) * 1000 >= thinking_suppress_ms:
                            thinking_phrase = get_random_thinking_phrase()
                            logger.info("→ TTS QUEUE [req#%d]: Enqueuing thinking phrase: '%s'", current_request_id, thinking_phrase)
                            await submit_tts(thinking_phrase, request_id=current_request_id)
                            last_thinking_phrase_ts = time.monotonic()
                        else:
                            logger.info("→ TTS QUEUE [req#%d]: Suppressing thinking phrase (said one %.0fms ago)", current_request_id, (time.monotonic() - last_thinking_phrase_ts) * 1000)
                        
                        if len(pending_transcripts) > initial_count:
                            additional_transcripts = pending_transcripts[initial_count:]
                            additional_text = _merge_incremental_transcript_parts([t[0] for t in additional_transcripts])
                            if additional_text:
                                combined_transcript = _merge_incremental_transcript_parts([combined_transcript, additional_text])
                            logger.info("⏱️ Quick answer escalating; collected %d additional transcripts during LLM call", len(additional_transcripts))
                            if additional_text:
                                print(f"\033[93m→ USER (continued): {additional_text}\033[0m", flush=True)
                        # Will send to gateway below
                except Exception as exc:
                    qa_elapsed = int((time.monotonic() - qa_start) * 1000) if 'qa_start' in locals() else 0
                    logger.error("Quick answer failed: %s; falling back to gateway (latency: %dms)", exc, qa_elapsed)
                    
                    # Play a thinking phrase on error too (suppress if one was said recently)
                    try:
                        thinking_suppress_ms = 12000
                        if last_thinking_phrase_ts is None or (time.monotonic() - last_thinking_phrase_ts) * 1000 >= thinking_suppress_ms:
                            thinking_phrase = get_random_thinking_phrase()
                            logger.info("→ TTS QUEUE [req#%d]: Enqueuing thinking phrase (error fallback): '%s'", current_request_id, thinking_phrase)
                            await submit_tts(thinking_phrase, request_id=current_request_id)
                            last_thinking_phrase_ts = time.monotonic()
                        else:
                            logger.info("→ TTS QUEUE [req#%d]: Suppressing thinking phrase (error fallback; said one %.0fms ago)", current_request_id, (time.monotonic() - last_thinking_phrase_ts) * 1000)
                    except Exception as tts_exc:
                        logger.error("Failed to play thinking phrase: %s", tts_exc)
                    
                    # Check for additional transcripts even on error
                    if len(pending_transcripts) > initial_count:
                        additional_transcripts = pending_transcripts[initial_count:]
                        additional_text = _merge_incremental_transcript_parts([t[0] for t in additional_transcripts])
                        if additional_text:
                            combined_transcript = _merge_incremental_transcript_parts([combined_transcript, additional_text])
                        logger.info("⏱️ Quick answer error; collected %d additional transcripts", len(additional_transcripts))
                        if additional_text:
                            print(f"\033[93m→ USER (continued): {additional_text}\033[0m", flush=True)
                    # Fall through to gateway
            
            # Gateway submission (if quick answer didn't handle it)
            if should_send_to_gateway:
                # Last-chance deterministic local handling for timer/alarm intents.
                # This prevents tool-eligible timer/alarm commands from leaking upstream
                # due to any earlier branch/bypass interactions.
                if timers_feature_enabled and tool_router:
                    try:
                        final_timer_result = await tool_router.try_deterministic_parse(combined_transcript)
                    except Exception as exc:
                        logger.debug("Final timer/alarm safety parse failed: %s", exc)
                        final_timer_result = None

                    if final_timer_result is not None:
                        from orchestrator.gateway.quick_answer import sanitize_quick_answer_text

                        local_response = sanitize_quick_answer_text(final_timer_result)
                        logger.info("✓ TIMER SAFETY-NET: handled locally before gateway dispatch")
                        startup_phase_active = False
                        if local_response:
                            print(f"\033[94m← TIMER SAFETY-NET: {local_response}\033[0m", flush=True)
                            logger.info(
                                "→ TTS QUEUE [req#%d]: Enqueuing timer safety-net response: '%s'",
                                current_request_id,
                                local_response[:80],
                            )
                            last_activity_ts = time.monotonic()
                            await submit_tts(local_response, request_id=current_request_id)
                            _safe_append_chat_message(
                                {
                                    "role": "assistant",
                                    "text": local_response,
                                    "tts_text": local_response,
                                    "source": "timer_safety_net",
                                    "request_id": current_request_id,
                                    "segment_kind": "final",
                                },
                                "timer_safety_net_assistant",
                            )
                            last_assistant_text = local_response
                            last_assistant_source = "timer_safety_net"
                            last_assistant_ts = time.monotonic()
                            last_assistant_was_question = assistant_turn_is_question(local_response)
                            last_assistant_expects_short_reply = assistant_turn_expects_short_reply(local_response)

                        should_send_to_gateway = False
                        last_user_went_upstream = False

                if not should_send_to_gateway:
                    return

                # Clear all pending transcripts now (we're sending everything)
                transcript_count = len(pending_transcripts)
                pending_transcripts.clear()

                # Keep startup/new-session gateway chatter suppressed until we actually
                # dispatch a user request upstream. This prevents delayed welcome or
                # out-of-order interim messages from interrupting request collation.
                if suppress_gateway_messages_for_new_session:
                    logger.info("🔓 Lifting gateway suppression at first upstream user dispatch [req#%d]", current_request_id)
                    suppress_gateway_messages_for_new_session = False
                startup_phase_active = False  # User message sent upstream; clear startup phase
                
                final_text = f"[{emotion_tag}] {combined_transcript}" if emotion_tag else combined_transcript

                if (
                    quick_answer_model_recommendation is not None
                    and getattr(gateway, "provider", "") == "openclaw"
                ):
                    resolved_model_id = resolve_recommended_model_id(
                        quick_answer_model_recommendation,
                        config,
                    )
                    if resolved_model_id:
                        final_text = f"/model {resolved_model_id} {final_text}"
                        logger.info(
                            "→ GATEWAY: Applied quick-answer model recommendation tier=%s as /model %s",
                            quick_answer_model_recommendation.get("tier", ""),
                            resolved_model_id,
                        )
                    else:
                        logger.info(
                            "→ GATEWAY: Quick-answer model recommendation present but no configured tier model resolved; sending transcript without /model prefix"
                        )

                logger.info("→ GATEWAY: Sending debounced transcript (%d parts) to %s [req#%d]", transcript_count, gateway.provider, current_request_id)
                last_gateway_send_ts = time.monotonic()
                last_user_went_upstream = True
                gw_start = time.monotonic()
                req_for_gateway = current_request_id
                _open_gateway_collation_window(req_for_gateway)
                response_text: str | None = None
                response_error_text = ""
                try:
                    response_text = await gateway.send_message(
                        final_text,
                        session_id=session_id,
                        agent_id=agent_id,
                        metadata={"emotion": emotion_tag} if emotion_tag else {},
                    )
                    gw_elapsed = int((time.monotonic() - gw_start) * 1000)
                    logger.info("← GATEWAY: Response received in %dms", gw_elapsed)
                except Exception as exc:
                    response_error_text = str(exc).strip() or "gateway request failed"
                    logger.warning("Gateway send failed (%s); continuing", response_error_text)
                streamed_fallback_text = ""
                if not response_text:
                    streamed_fallback_text = _latest_stream_text_for_request(req_for_gateway)
                    if streamed_fallback_text:
                        response_text = streamed_fallback_text
                        logger.info(
                            "↩️ Using streamed fallback text for finalization [req#%d] (%d chars)",
                            req_for_gateway,
                            len(streamed_fallback_text),
                        )

                if response_text or response_error_text:
                    if response_text:
                        print(f"\033[94m← ASSISTANT: {response_text}\033[0m", flush=True)

                    summary = None
                    summary_user_prompt = _collect_summary_user_prompt(combined_transcript)
                    summary_topic = _summary_topic_hint(summary_user_prompt)
                    target_summary_words = max(1, int(config.tts_long_response_summary_target_words))
                    summary_trigger_words = max(1, int(config.tts_long_response_summary_word_trigger))
                    summary_source_text = response_text or response_error_text
                    normalized_response = re.sub(r"\s+", " ", response_text or "").strip()
                    response_word_count = _count_spoken_words_for_tts(normalized_response) if normalized_response else 0
                    summary_enabled = bool(config.tts_long_response_summary_enabled)
                    should_summarize = bool(
                        summary_enabled
                        and (
                            response_error_text
                            or response_word_count >= summary_trigger_words
                        )
                    )

                    if quick_answer_client is not None and should_summarize:
                        try:
                            summary = await quick_answer_client.summarize_for_tts(
                                summary_source_text,
                                target_words=target_summary_words,
                                timeout_ms=int(config.tts_long_response_summary_timeout_ms),
                                user_question=summary_user_prompt,
                            )
                        except Exception as exc:
                            logger.warning("Gateway TTS summarization failed [req#%d]: %s", req_for_gateway, exc)

                    fallback_summary = ""
                    if response_error_text and normalized_response:
                        if summary_topic:
                            fallback_summary = f"I hit an error after partial output for your request about {summary_topic}, but I preserved and returned what was received."
                        else:
                            fallback_summary = "I hit an error after partial output, but I preserved and returned the received response."
                    elif response_error_text:
                        fallback_summary = f"I could not complete your request due to an error: {response_error_text}."
                    elif should_summarize and summary_topic:
                        fallback_summary = f"I completed your request about {summary_topic} and returned the result."
                    elif should_summarize and normalized_response:
                        summary_tokens = normalized_response.split()
                        fallback_summary = " ".join(summary_tokens[:target_summary_words]).strip()
                        if len(summary_tokens) > target_summary_words:
                            fallback_summary = fallback_summary.rstrip(" ,;:") + "…"

                    if summary and normalized_response and _summary_looks_like_response_excerpt(summary, normalized_response):
                        logger.info("↩️ Replacing excerpt-like summary with user-focused fallback [req#%d]", req_for_gateway)
                        summary = ""

                    if should_summarize and not summary:
                        summary = fallback_summary

                    spoken_text = (response_text or "").strip()
                    if summary:
                        if response_text:
                            raw_words = _count_spoken_words_for_tts(response_text)
                            summary_words = _count_spoken_words_for_tts(summary)
                            logger.info(
                                "🗜️ Gateway response summary [req#%d]: raw=%d words -> summary=%d words",
                                req_for_gateway,
                                raw_words,
                                summary_words,
                            )
                        if not config.gateway_tts_streaming_enabled or not spoken_text:
                            spoken_text = summary
                    elif not config.gateway_tts_streaming_enabled and response_text:
                        logger.info(
                            "↩️ Gateway TTS summary unavailable [req#%d]; speaking full response",
                            req_for_gateway,
                        )

                    chat_text = summary or spoken_text or (response_error_text or "Request failed.")
                    if response_error_text:
                        chat_text = f"Error: {response_error_text}. {chat_text}".strip()

                    if spoken_text:
                        logger.info("→ TTS QUEUE [req#%d]: Enqueuing response: '%s'", req_for_gateway, spoken_text[:80])
                        # Update activity timestamp to keep system awake during TTS synthesis
                        last_activity_ts = time.monotonic()
                        await submit_tts(spoken_text, request_id=req_for_gateway)
                    else:
                        last_activity_ts = time.monotonic()

                    chat_message: dict[str, Any] = {
                        "role": "assistant",
                        "text": chat_text,
                        "tts_text": spoken_text,
                        "source": "gateway",
                        "request_id": req_for_gateway,
                        "segment_kind": "final",
                    }
                    if response_error_text:
                        chat_message["error"] = response_error_text
                    if normalized_response and chat_text.strip() != normalized_response:
                        chat_message["full_text"] = response_text
                    _safe_append_chat_message(
                        chat_message,
                        "gateway_assistant",
                    )

                    effective_assistant_text = response_text or chat_text
                    last_assistant_text = effective_assistant_text
                    last_assistant_source = "gateway"
                    last_assistant_ts = time.monotonic()
                    last_assistant_was_question = assistant_turn_is_question(effective_assistant_text)
                    last_assistant_expects_short_reply = assistant_turn_expects_short_reply(effective_assistant_text)
                    last_upstream_assistant_text = effective_assistant_text
                    last_upstream_assistant_ts = last_assistant_ts
                    last_upstream_response_was_question = last_assistant_was_question
                    last_upstream_response_requested_confirmation = last_assistant_expects_short_reply
                else:
                    # Agent executed command without returning text (e.g., "play jazz")
                    logger.info("← GATEWAY: No text response (agent executed action without speech response)")
                    # Update activity timestamp but don't queue TTS
                    last_activity_ts = time.monotonic()
                _schedule_gateway_collation_close(
                    req_for_gateway,
                    reason="gateway_send_complete",
                    # Keep this longer than gateway listener flush delay (5s).
                    delay_s=6.5,
                )
        finally:
            _push_schedule_state_now("request_complete")
            # Always clear processing flag
            processing_request = False
            
            # If new transcripts arrived while we were processing, restart debounce timer
            if pending_transcripts:
                logger.info("⏱️ New transcripts arrived during processing (%d pending); restarting debounce", len(pending_transcripts))
                if debounce_task and not debounce_task.done():
                    debounce_task.cancel()
                debounce_task = asyncio.create_task(send_debounced_transcripts())

    def count_syllables(word: str) -> int:
        """Estimate syllable count based on vowel groups."""
        word = word.lower()
        if not word:
            return 0
        vowels = "aeiouy"
        syllable_count = 0
        previous_was_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not previous_was_vowel:
                syllable_count += 1
            previous_was_vowel = is_vowel
        return max(1, syllable_count)

    def canonicalize_transcript_for_match(text: str) -> str:
        """Lowercase transcript and remove punctuation for phrase matching."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        # Normalize apostrophes, then keep only word-ish content.
        lowered = lowered.replace("’", "'")
        lowered = re.sub(r"[^a-z0-9\s']+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    def normalize_transcript(transcript: str) -> str:
        """Normalize STT text and drop blank/punctuation-only markers before routing."""
        text = (transcript or "").strip()
        if not text:
            return ""

        # Ignore descriptor-only transcripts such as "(knocking on door)",
        # "[door closes]", or "*door slams*" where no actual spoken words are present.
        without_bracket_descriptors = re.sub(r"[\(\[][^\)\]]*[\)\]]|\*[^*]+\*", " ", text)
        if not re.search(r"[a-zA-Z0-9]", without_bracket_descriptors):
            logger.info("⊘ Transcript filtered: bracketed sound descriptor only ('%s')", text[:120])
            return ""

        lowered = text.lower()
        ignore_markers = (
            "[inaudible]",
            "[blank_audio]",
            "blank_audio",
        )
        if any(marker in lowered for marker in ignore_markers):
            return ""

        # Remove bracketed non-speech markers and repeated punctuation-only filler.
        text = re.sub(r"\[(?:[^\]]+)\]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        has_words = bool(re.search(r"[a-zA-Z0-9]", text))
        if not has_words:
            return ""

        return text

    def _is_schedule_intent_transcript(text: str) -> bool:
        """Return True when transcript looks like a timer/alarm creation request.

        Used to bypass debounce for near-instant schedule actions in the UI.
        """
        canonical = canonicalize_transcript_for_match(text)
        if not canonical:
            return False
        has_duration = bool(
            re.search(r"\b\d+(?:\.\d+)?\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", canonical)
            or re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|half|couple)\b.*\b(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", canonical)
        )
        if not has_duration:
            return False
        return bool(
            re.search(r"\b(set|create|start)\b.*\b(timer|alarm)\b", canonical)
            or re.search(r"\b(wake me|wake us|alarm me|remind me)\b.*\b(in|for)\b", canonical)
        )

    def _is_alarm_stop_intent_transcript(text: str) -> bool:
        canonical = canonicalize_transcript_for_match(text)
        if not canonical:
            return False
        if canonical in {
            "stop",
            "stop stop",
            "stop stop stop",
            "silence",
            "cancel",
            "quiet",
            "enough",
            "shut up",
        }:
            return True

        stop_token = bool(re.search(r"\b(stop|silence|cancel|quiet|enough|dismiss|off|shut up)\b", canonical))
        alarm_token = bool(re.search(r"\b(alarm|alarms|ringing|ring|bell|beeping|beep)\b", canonical))
        if stop_token and alarm_token:
            return True

        return bool(
            re.search(r"\b(turn|switch)\s+off\s+(the\s+)?alarm(s)?\b", canonical)
            or re.search(r"\bmake\s+it\s+stop\b", canonical)
            or re.search(r"\bstop\s+it\b", canonical)
            or re.search(r"\bplease\s+stop\b", canonical)
        )

    def _is_incremental_prefix_extension(previous_text: str, next_text: str) -> bool:
        prev = canonicalize_transcript_for_match(previous_text)
        nxt = canonicalize_transcript_for_match(next_text)
        return bool(prev and nxt and nxt.startswith(prev))

    def enqueue_pending_transcript(transcript: str, emotion_tag: str) -> bool:
        nonlocal pending_transcripts

        normalized = normalize_transcript(transcript)
        if not normalized:
            return False

        if pending_transcripts:
            last_text, last_emotion = pending_transcripts[-1]
            if _is_incremental_prefix_extension(last_text, normalized):
                pending_transcripts[-1] = (normalized, emotion_tag or last_emotion)
                logger.info("⏱️ Transcript updated for debounce (%d pending)", len(pending_transcripts))
                return True
            if _is_incremental_prefix_extension(normalized, last_text):
                logger.info(
                    "⏱️ Transcript ignored as shorter incremental duplicate (%d pending)",
                    len(pending_transcripts),
                )
                return False

        pending_transcripts.append((normalized, emotion_tag))
        logger.info("⏱️ Transcript queued for debounce (%d pending)", len(pending_transcripts))
        return True

    def is_likely_tts_self_echo(transcript: str, now_ts: float) -> bool:
        """Best-effort echo suppression for transcripts captured from speaker playback."""
        canonical = canonicalize_transcript_for_match(transcript)
        if not canonical:
            return False

        # While TTS is active, aggressively suppress common short acknowledgements
        # that are repeatedly re-captured from the speaker path.
        if tts_playing and canonical in {
            "thank you",
            "thanks",
            "thanks for watching",
            "you re welcome",
            "you're welcome",
            "youre welcome",
            "i'm sorry",
            "im sorry",
            "i don't know",
            "i dont know",
        }:
            return True

        # Only apply similarity checks while TTS is active or shortly after the
        # most recent TTS enqueue time.
        recent_window_s = 6.0
        if not tts_playing and (now_ts - last_tts_ts) > recent_window_s:
            return False

        candidates: list[str] = []
        if last_tts_text:
            candidates.append(canonicalize_transcript_for_match(last_tts_text))
        if current_tts_text:
            candidates.append(canonicalize_transcript_for_match(current_tts_text))

        tx_words = canonical.split()
        tx_set = set(tx_words)
        for candidate in candidates:
            if not candidate:
                continue
            if canonical == candidate:
                return True
            if len(canonical) >= 8 and (canonical in candidate or candidate in canonical):
                return True

            cand_words = candidate.split()
            cand_set = set(cand_words)
            if not tx_set or not cand_set:
                continue
            overlap = len(tx_set & cand_set) / float(max(len(tx_set), 1))
            candidate_overlap = len(tx_set & cand_set) / float(max(len(cand_set), 1))
            if overlap >= 0.75 and candidate_overlap >= 0.6 and len(tx_words) <= 12:
                return True

        return False

    async def process_chunk(
        pcm: bytes,
        cut_in_ts: float | None = None,
        chunk_started_ts: float | None = None,
        recorder_capture_mode: bool = False,
    ) -> None:
        nonlocal active_transcriptions, state, pending_transcripts, debounce_task
        nonlocal cut_in_tts_hold_active, cut_in_tts_hold_started_ts, cut_in_tts_hold_request_id
        nonlocal tts_playing, last_tts_text, last_tts_ts, current_tts_text
        nonlocal wake_state, wake_sleep_ts, last_wake_detected_ts
        nonlocal recorder_stop_hotword_armed_ts
        nonlocal last_assistant_ts, last_assistant_was_question, last_assistant_expects_short_reply
        nonlocal last_user_went_upstream, last_upstream_assistant_ts, last_upstream_response_was_question
        nonlocal last_upstream_response_requested_confirmation
        nonlocal ghost_suppressed_total, ghost_suppressed_short_no_question, ghost_suppressed_self_echo
        nonlocal ghost_accepted_short_after_question, ghost_accepted_short_after_upstream_question
        nonlocal processing_request
        active_transcriptions += 1
        state = VoiceState.SENDING
        try:
            wav_bytes = pcm_to_wav_bytes(pcm, config.audio_sample_rate)
            
            # STT phase
            logger.info("→ STT: Sending %d bytes to Whisper", len(wav_bytes))
            stt_start = time.monotonic()
            try:
                transcript = await asyncio.to_thread(whisper_client.transcribe, wav_bytes)
            except Exception as exc:
                logger.error("Whisper transcription failed: %s", exc)
                transcript = "[inaudible]"
            stt_elapsed = int((time.monotonic() - stt_start) * 1000)
            logger.info("← STT: Complete in %dms: '%s'", stt_elapsed, transcript[:80])
                
            transcript = normalize_transcript(transcript)

            # Filter out transcripts that are blank audio, inaudible, or punctuation-only
            if not transcript:
                logger.warning(
                    "⊘ Transcript filtered out after normalization (blank audio / inaudible / punctuation-only)"
                )
                return

            # Suppress standalone filler/acknowledgement transcriptions that are
            # STT hallucinations or ambient sounds with no real spoken intent.
            _FILLER_PHRASE_FILTER = {"thank you", "all right", "alright"}
            if canonicalize_transcript_for_match(transcript) in _FILLER_PHRASE_FILTER:
                logger.info("⊘ Transcript suppressed by phrase filter: '%s'", transcript[:80])
                return

            now_ts = time.monotonic()
            canonical_transcript = canonicalize_transcript_for_match(transcript)
            if alarm_manager and alarm_manager.ringing_alarms:
                if _is_alarm_stop_intent_transcript(transcript):
                    dismissed_by_phrase = await stop_ringing_alarms_immediately("voice stop phrase")
                    if dismissed_by_phrase > 0:
                        logger.info("✋ Voice stop phrase dismissed active alarm ringing: '%s'", transcript[:80])
                else:
                    logger.info("⏰ Ignoring transcript while alarm is ringing: '%s'", transcript[:80])
                logger.info("⏰ Swallowing transcript while alarm is ringing: '%s'", transcript[:80])
                return
            token_count = len([t for t in canonical_transcript.split() if t])
            is_single_word = token_count == 1
            is_short_transcript = token_count <= 3
            ms_since_tts_end = max(0.0, (now_ts - last_tts_ts) * 1000)
            ms_since_last_assistant_turn = (
                max(0.0, (now_ts - last_assistant_ts) * 1000)
                if last_assistant_ts is not None
                else float("inf")
            )
            ms_since_upstream_assistant_turn = (
                max(0.0, (now_ts - last_upstream_assistant_ts) * 1000)
                if last_upstream_assistant_ts is not None
                else float("inf")
            )
            upstream_context_is_fresh = bool(
                last_user_went_upstream
                and ms_since_upstream_assistant_turn <= float(config.ghost_filter_upstream_context_ms)
            )

            cut_in_active = cut_in_ts is not None
            ms_from_cut_in_start = float("inf")
            if cut_in_active:
                reference_ts = chunk_started_ts if chunk_started_ts is not None else now_ts
                ms_from_cut_in_start = max(0.0, (reference_ts - cut_in_ts) * 1000)

            self_echo_similarity = score_self_echo_similarity(
                transcript,
                [last_tts_text, current_tts_text],
            )

            has_fresh_prompt_context = (
                ms_since_last_assistant_turn <= float(config.ghost_filter_recent_assistant_ms)
                and (last_assistant_was_question or last_assistant_expects_short_reply)
            )

            ghost_ctx: dict[str, Any] = {
                "transcript_text": transcript,
                "canonical_transcript": canonical_transcript,
                "token_count": token_count,
                "char_count": len(transcript),
                "is_single_word": is_single_word,
                "is_short_transcript": is_short_transcript,
                "tts_playing": tts_playing,
                "ms_since_tts_end": ms_since_tts_end,
                "last_assistant_was_question": last_assistant_was_question,
                "last_assistant_expects_short_reply": last_assistant_expects_short_reply,
                "last_user_went_upstream": last_user_went_upstream,
                "last_upstream_response_was_question": last_upstream_response_was_question,
                "last_upstream_response_requested_confirmation": last_upstream_response_requested_confirmation,
                "upstream_context_is_fresh": upstream_context_is_fresh,
                "cut_in_active": cut_in_active,
                "ms_from_cut_in_start": ms_from_cut_in_start,
                "self_echo_similarity": self_echo_similarity,
                "self_echo_similarity_threshold": float(config.ghost_filter_self_echo_similarity_threshold),
                "single_word_enabled": bool(config.ghost_filter_single_word_enabled),
                "require_question_for_acks": bool(config.ghost_filter_require_question_for_acks),
                "playback_tail_ms": float(config.ghost_filter_playback_tail_ms),
                "cutin_early_ms": float(config.ghost_filter_cutin_early_ms),
                "has_fresh_prompt_context": has_fresh_prompt_context,
                "has_inflight_user_request": bool(processing_request or len(pending_transcripts) > 0),
                "recorder_active": bool(recorder_capture_mode or (recorder_tool and recorder_tool.is_recording())),
            }

            ghost_filter_active = bool(config.ghost_filter_enabled and not config.ghost_filter_kill_switch)
            if ghost_filter_active:
                decision = decide_ghost_transcript(ghost_ctx)
                if not decision.accepted:
                    ghost_suppressed_total += 1
                    if "self_echo" in decision.matched_priority_rule:
                        ghost_suppressed_self_echo += 1
                    if "short" in decision.matched_priority_rule or "ack_no_question" in decision.matched_priority_rule:
                        ghost_suppressed_short_no_question += 1
                    if config.ghost_filter_debug_logging:
                        logger.warning(
                            "⊘ Ghost transcript suppressed: rule=%s score=%d reasons=%s text='%s' (tts_playing=%s ms_since_tts_end=%d ms_since_asst=%d cut_in_ms=%d sim=%.2f)",
                            decision.matched_priority_rule,
                            decision.score,
                            ",".join(decision.reason_codes),
                            transcript[:120],
                            tts_playing,
                            int(ms_since_tts_end),
                            int(ms_since_last_assistant_turn if math.isfinite(ms_since_last_assistant_turn) else -1),
                            int(ms_from_cut_in_start if math.isfinite(ms_from_cut_in_start) else -1),
                            self_echo_similarity,
                        )
                    return
                if is_short_transcript and last_assistant_was_question:
                    ghost_accepted_short_after_question += 1
                if is_short_transcript and last_user_went_upstream and last_upstream_response_was_question:
                    ghost_accepted_short_after_upstream_question += 1

            if recorder_tool and recorder_capture_mode:
                recorder_stop_armed = recorder_stop_hotword_armed_ts is not None
                if recorder_stop_armed and recorder_tool.should_stop_from_transcript(transcript):
                    dynamic_trim_seconds = compute_hotword_stop_trim_seconds(
                        armed_ts=recorder_stop_hotword_armed_ts,
                        stop_ts=now_ts,
                        extra_trim_ms=int(config.recorder_stop_hotword_extra_trim_ms),
                        max_trim_ms=int(config.recorder_stop_hotword_max_trim_ms),
                    )
                    stop_result = await recorder_tool.stop_recording(
                        reason="voice stop phrase",
                        trim_tail_seconds=dynamic_trim_seconds,
                    )
                    logger.info(
                        "🎙️ Recorder stop trim applied from hotword arm: %.3fs (armed_age=%.3fs)",
                        dynamic_trim_seconds,
                        max(0.0, now_ts - float(recorder_stop_hotword_armed_ts or now_ts)),
                    )
                    recorder_stop_hotword_armed_ts = None
                    _append_recorder_finished_chat(stop_result)
                    if stop_result.response:
                        await submit_tts(stop_result.response, request_id=current_request_id, kind="notification")
                    logger.info("🎙️ Recorder stop phrase handled locally after hotword arm")
                elif recorder_tool.should_stop_from_transcript(transcript):
                    logger.info("🎙️ Recorder stop phrase ignored until hotword arm is active")
                else:
                    logger.info("🎙️ Recorder transcript ignored for web chat/gateway while capture mode is active")
                wake_state = WakeState.ASLEEP
                wake_sleep_ts = now_ts
                last_wake_detected_ts = None
                recorder_stop_hotword_armed_ts = None
                return

            start_recording_intent = bool(
                re.search(r"\b(start|begin)\s+(the\s+)?record(ing)?\b", canonical_transcript)
                or re.search(r"\brecorder\s+on\b", canonical_transcript)
            )
            if start_recording_intent:
                # Recorder start path runs before normal debounced chat routing, so mirror
                # the user command immediately to chat here.
                _safe_append_chat_message(
                    {
                        "role": "user",
                        "text": transcript,
                        "source": "voice",
                        "request_id": current_request_id,
                    },
                    "voice_user_recorder_start",
                )
                if recorder_tool:
                    # Confirm in chat + TTS before recording activation so the confirmation
                    # is visible/spoken first when TTS is enabled.
                    kickoff_text = "Recording started."
                    _safe_append_chat_message(
                        {
                            "role": "assistant",
                            "text": kickoff_text,
                            "tts_text": kickoff_text,
                            "source": "recorder_local",
                            "request_id": current_request_id,
                            "segment_kind": "final",
                        },
                        "recorder_start_assistant",
                    )
                    await submit_tts(kickoff_text, request_id=current_request_id, kind="notification")

                    start_result = await recorder_tool.start_recording()
                    if not bool(start_result.get("success", False)):
                        err_text = str(start_result.get("response", "Recording could not be started.")).strip()
                        if err_text:
                            _safe_append_chat_message(
                                {
                                    "role": "assistant",
                                    "text": err_text,
                                    "tts_text": err_text,
                                    "source": "recorder_local",
                                    "request_id": current_request_id,
                                    "segment_kind": "final",
                                },
                                "recorder_start_error_assistant",
                            )
                            await submit_tts(err_text, request_id=current_request_id, kind="notification")
                    logger.info("🎙️ Recorder start phrase handled locally before gateway routing")
                else:
                    disabled_text = "Recording is not enabled on this device."
                    _safe_append_chat_message(
                        {
                            "role": "assistant",
                            "text": disabled_text,
                            "tts_text": disabled_text,
                            "source": "recorder_local",
                            "request_id": current_request_id,
                            "segment_kind": "final",
                        },
                        "recorder_start_disabled_assistant",
                    )
                    await submit_tts(
                        disabled_text,
                        request_id=current_request_id,
                        kind="notification",
                    )
                    logger.info("🎙️ Recorder start phrase handled locally (recorder disabled)")
                return
            
            # Emotion detection phase
            emotion_tag = ""
            if config.emotion_enabled:
                logger.info("→ EMOTION: Detecting emotional state")
                emotion_start = time.monotonic()
                try:
                    emotion_tag = await asyncio.wait_for(
                        asyncio.to_thread(emotion.detect_emotion, wav_bytes),
                        timeout=config.emotion_timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    emotion_tag = ""
                emotion_elapsed = int((time.monotonic() - emotion_start) * 1000)
                if emotion_tag:
                    logger.info("← EMOTION: Detected '%s' in %dms", emotion_tag, emotion_elapsed)
                else:
                    logger.info("← EMOTION: No emotion detected (%dms)", emotion_elapsed)

            # Add to pending transcripts and restart debounce timer
            if cut_in_tts_hold_active:
                cut_in_tts_hold_active = False
                cut_in_tts_hold_started_ts = None
                cut_in_tts_hold_request_id = 0
                logger.info("🔓 Cut-in TTS hold released after successful transcript")

            if not enqueue_pending_transcript(transcript, emotion_tag):
                return
            
            # Cancel existing debounce task and start new one
            immediate_schedule_dispatch = _is_schedule_intent_transcript(transcript)
            immediate_music_dispatch = bool(music_router and music_router.is_music_related(transcript))
            immediate_dispatch = immediate_schedule_dispatch or immediate_music_dispatch
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()
            debounce_task = asyncio.create_task(send_debounced_transcripts(immediate=immediate_dispatch))
            if immediate_schedule_dispatch:
                logger.info("⏩ Debounce bypass: immediate dispatch for schedule intent")
            elif immediate_music_dispatch:
                logger.info("⏩ Debounce bypass: immediate dispatch for music intent")
        finally:
            active_transcriptions = max(0, active_transcriptions - 1)
            if active_transcriptions == 0:
                state = VoiceState.IDLE

    async def tts_loop() -> None:
        nonlocal tts_playing, tts_gain, last_playback_frame, tts_playback_start_ts
        nonlocal current_tts_text, current_tts_duration_s
        nonlocal current_tts_request_id, last_activity_ts
        nonlocal tts_last_played_request_id
        nonlocal wake_state, wake_sleep_ts, last_wake_detected_ts
        while True:
            item = await _tts_dequeue()
            text = item.text
            request_id = item.request_id
            if not text:
                continue

            if item.kind == "reply" and request_id < current_request_id:
                logger.info("🚫 Dropped stale reply before playback [req#%d < current#%d]", request_id, current_request_id)
                continue

            if web_service and bool(web_service._ui_control_state.get("tts_muted", False)) and not item.allow_when_ui_tts_muted:
                logger.info("🔇 TTS muted by UI; skipping %s playback for req#%d", item.kind, request_id)
                continue

            while True:
                now_ts = time.monotonic()
                if item.kind == "reply" and request_id < current_request_id:
                    logger.info("🚫 Dropped stale reply during gate wait [req#%d < current#%d]", request_id, current_request_id)
                    text = ""
                    break
                blocked_reason = tts_start_gate_block_reason(
                    item_kind=item.kind,
                    now_ts=now_ts,
                    cut_in_tts_hold_active=cut_in_tts_hold_active,
                    tts_playing=tts_playing,
                    item_request_id=request_id,
                    tts_last_played_request_id=tts_last_played_request_id,
                    state=state,
                    listening_state=VoiceState.LISTENING,
                    last_speech_ts=last_speech_ts,
                    vad_min_silence_ms=config.vad_min_silence_ms,
                )
                if blocked_reason is None:
                    break
                await asyncio.sleep(0.08)

            if not text:
                continue

            tts_playing = True
            current_tts_text = text
            current_tts_request_id = request_id  # Track which request is now playing
            current_tts_duration_s = 0.0
            logger.info("▶️ TTS PLAY: Starting playback for [req#%d] (gain=%.1f)", request_id, tts_gain)
            # Reset wake timeout when TTS starts to keep conversation alive
            last_activity_ts = time.monotonic()
            try:
                try:
                    # TTS synthesis phase
                    logger.info("→ TTS SYNTH [req#%d]: Generating speech for: '%s'", request_id, text[:80])
                    synth_start = time.monotonic()
                    wav_bytes = await asyncio.to_thread(piper.synthesize, text, config.piper_voice_id, config.piper_speed)
                    synth_elapsed = int((time.monotonic() - synth_start) * 1000)
                    logger.info("← TTS SYNTH: Generated %d bytes in %dms", len(wav_bytes), synth_elapsed)

                    # Playback phase
                    effective_tts_gain = max(0.05, float(tts_gain * config.tts_relative_gain))
                    # Apply dynamic volume reduction if cut-in tracker indicates it
                    vol_multiplier = cut_in_tracker.get_output_volume_multiplier()
                    if vol_multiplier < 1.0:
                        effective_tts_gain *= vol_multiplier
                    logger.info(
                        "→ TTS PLAY: Starting playback (base_gain=%.2f, relative=%.2f, effective=%.2f, cut_in_vol_mult=%.2f)",
                        tts_gain,
                        config.tts_relative_gain,
                        effective_tts_gain,
                        vol_multiplier,
                    )
                    # Reset Silero RNN state to prevent carryover from previous speech
                    if cut_in_silero is not None:
                        cut_in_silero.reset_state()
                    tts_playback_start_ts = time.monotonic()
                    play_start = time.monotonic()
                    pcm, wav_rate = wav_bytes_to_pcm_with_rate(wav_bytes)
                    # Resample to playback rate (which may differ from capture rate for USB devices)
                    target_rate = playback.sample_rate
                    if wav_rate != target_rate:
                        pcm = resample_pcm(pcm, wav_rate, target_rate)
                    sample_count = len(pcm) / 2.0
                    current_tts_duration_s = sample_count / float(target_rate) if sample_count > 0 else 0.0
                    tts_stop_event.clear()
                    await asyncio.to_thread(playback.play_pcm, pcm, effective_tts_gain, tts_stop_event)
                    play_elapsed = int((time.monotonic() - play_start) * 1000)
                    interrupted = tts_stop_event.is_set()
                    if interrupted:
                        logger.info("⏹️ TTS PLAY: Interrupted by mic speech (%dms)", play_elapsed)
                    else:
                        logger.info("← TTS PLAY: Playback complete in %dms", play_elapsed)
                        # Reset wake timeout after TTS completes to keep conversation alive
                        last_activity_ts = time.monotonic()
                        if config.wake_word_enabled:
                            wake_state = WakeState.AWAKE
                            wake_sleep_ts = None
                            last_wake_detected_ts = last_activity_ts
                            logger.info("🌙 Wake state kept AWAKE after TTS completion")
                    last_playback_frame = None
                    tts_playback_start_ts = None
                    if not interrupted:
                        tts_last_played_request_id = request_id
                        logger.info("↻ Restarting audio capture after TTS playback")
                        try:
                            capture.restart()
                        except Exception as exc:  # pragma: no cover
                            logger.warning("Audio capture restart failed: %s", exc)
                    else:
                        logger.info("↻ Skipping capture restart after cut-in interruption")
                except Exception as exc:
                    logger.error("Piper TTS failed: %s", exc)
            finally:
                tts_playing = False
                tts_gain = tts_base_gain
                current_tts_text = ""
                current_tts_duration_s = 0.0

    async def gateway_listener() -> None:
        nonlocal dropped_out_of_window_gateway_frames
        buffer = ""
        flush_task: asyncio.Task | None = None
        flush_delay_s = 5.0
        tts_streaming_enabled = bool(config.gateway_tts_streaming_enabled)
        first_chunk_word_threshold = max(0, config.gateway_tts_fast_start_words) if tts_streaming_enabled else 0
        active_buffer_request_id = 0
        ui_stream_state_by_request_id: dict[int, dict[str, str]] = {}
        kickoff_sent_request_id = 0
        reconnect_delay_s = 1.0
        reconnect_delay_max_s = 8.0

        logger.info(
            "🔊 Gateway TTS mode: %s",
            "streaming sentence chunks" if tts_streaming_enabled else "single final response with summarization",
        )

        def _push_ui_stream_chunk(request_id: int, chunk_text: str) -> None:
            if not web_service or request_id <= 0:
                return
            if not chunk_text:
                return

            stream_state = ui_stream_state_by_request_id.get(request_id)
            if stream_state is None:
                stream_state = {
                    "id": f"assistant-stream-{request_id}",
                    "text": "",
                }
                ui_stream_state_by_request_id[request_id] = stream_state
                if len(ui_stream_state_by_request_id) > 64:
                    stale_request_ids = sorted(ui_stream_state_by_request_id.keys())[:-32]
                    for stale_request_id in stale_request_ids:
                        ui_stream_state_by_request_id.pop(stale_request_id, None)

            stream_state["text"] += chunk_text
            stream_msg = {
                "id": stream_state["id"],
                "role": "assistant",
                "text": stream_state["text"],
                "source": "gateway_stream",
                "request_id": request_id,
                "segment_kind": "stream",
            }
            if hasattr(web_service, "upsert_chat_message"):
                web_service.upsert_chat_message(stream_msg)
            else:
                web_service.append_chat_message(stream_msg)

        def _payload_short_text(value: Any, limit: int = 280) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                s = value.strip()
            else:
                try:
                    s = json.dumps(value, ensure_ascii=False)
                except Exception:
                    s = str(value)
            if len(s) > limit:
                return s[: limit - 1] + "…"
            return s

        def _extract_structured_event(payload: dict[str, Any], request_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
            event_type = str(payload.get("event") or payload.get("type") or "").strip().lower()
            phase = str(payload.get("phase") or payload.get("status") or payload.get("event") or payload.get("type") or "update")
            tool_call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("call_id") or payload.get("callId")
            tool_name = payload.get("tool_name") or payload.get("tool") or payload.get("name") or payload.get("function")
            has_tool_fields = any(
                k in payload
                for k in ("tool_call_id", "toolCallId", "tool_name", "tool", "args", "arguments", "result", "partialResult", "output", "stdout", "stderr")
            )
            is_tool_event = has_tool_fields or ("tool" in event_type and event_type not in {""})
            is_reasoning_event = event_type in {"lifecycle", "reasoning", "compaction", "intermediate", "phase"}

            if not is_tool_event and not is_reasoning_event:
                return None, None, False

            details_json = json.dumps(payload, ensure_ascii=False)
            req_id = int(request_id)

            if is_tool_event:
                name = str(tool_name or "tool")
                step_msg = {
                    "role": "step",
                    "text": name,
                    "name": name,
                    "phase": phase,
                    "request_id": req_id,
                    "tool_call_id": str(tool_call_id or ""),
                    "details": details_json,
                    "source": "gateway_stream",
                }
                summary_raw = (
                    payload.get("result")
                    or payload.get("partialResult")
                    or payload.get("output")
                    or payload.get("stdout")
                    or payload.get("stderr")
                    or payload.get("message")
                )
                summary = _payload_short_text(summary_raw)
                interim_msg = None
                if summary:
                    interim_msg = {
                        "role": "interim",
                        "text": name,
                        "phase": phase,
                        "request_id": req_id,
                        "details": json.dumps({"text": summary}, ensure_ascii=False),
                        "source": "gateway_stream",
                    }
                return step_msg, interim_msg, True

            label = str(payload.get("name") or payload.get("phase") or payload.get("event") or payload.get("type") or "lifecycle")
            interim_msg = {
                "role": "interim",
                "text": label,
                "phase": phase,
                "request_id": req_id,
                "details": details_json,
                "source": "gateway_stream",
            }
            return None, interim_msg, True

        def should_emit_fast_start_chunk(text: str) -> bool:
            """Avoid early TTS kickoff at awkward clause boundaries (e.g., ending with 'so')."""
            s = text.strip()
            if not s:
                return False
            if s[-1] in ".!?":
                return True
            # Avoid kickoff on connector words that usually imply continuation.
            tokens = re.findall(r"[A-Za-z']+", s.lower())
            if not tokens:
                return False
            trailing_connectors = {
                "and", "or", "but", "so", "because", "if", "then", "than", "though", "although",
                "however", "therefore", "thus", "while", "when", "where", "which", "that", "who",
                "whom", "whose", "to", "of", "in", "on", "at", "for", "with", "from", "by",
                "a", "an", "the",
            }
            return tokens[-1] not in trailing_connectors

        def split_first_n_words(text: str, n: int) -> tuple[str, str, bool]:
            """Split text into first n tokens and remainder.

            Returns (prefix, remainder, boundary_safe). boundary_safe is False when
            the split lands at end-of-buffer on an alphanumeric character, which can
            indicate a streamed partial word.
            """
            if n <= 0:
                return "", text, False
            tokens = list(re.finditer(r"\S+", text))
            if len(tokens) < n:
                return "", text, False
            cutoff = tokens[n - 1].end()
            prefix = text[:cutoff].strip()
            remainder_raw = text[cutoff:]
            remainder = remainder_raw.strip()
            if remainder:
                return prefix, remainder, True
            trailing = text[cutoff - 1] if cutoff > 0 else ""
            boundary_safe = bool(trailing) and not trailing.isalnum()
            return prefix, remainder, boundary_safe

        async def flush_buffer(request_id: int) -> None:
            nonlocal buffer
            if not buffer.strip():
                buffer = ""
                return

            if not _touch_gateway_collation_window(request_id):
                logger.info("🧩 Dropped buffered gateway text outside active window [req#%d]", request_id)
                buffer = ""
                return

            if suppress_gateway_messages_for_new_session:
                logger.info("🔇 Suppressed buffered gateway output after new-session reset")
                buffer = ""
                return
            
            # During startup phase, only suppress welcome greetings, not all output
            if startup_phase_active and buffer:
                text_to_check = strip_gateway_control_markers(buffer).strip()
                if is_startup_welcome_pattern(text_to_check):
                    logger.info("🔇 Suppressed startup welcome pattern from buffer: '%s'...", text_to_check[:60])
                    buffer = ""
                    return
            
            # Filter out NO_REPLY markers
            text_to_send = strip_gateway_control_markers(buffer).strip()
            if "NO_REPLY" in text_to_send or "NO_RE" in text_to_send or text_to_send in ["NO", "_RE", "NO _RE"]:
                logger.info("🚫 Filtered NO_REPLY from flush: '%s'", text_to_send)
                buffer = ""
                return
                
            if tts_streaming_enabled:
                await submit_tts(text_to_send, request_id=request_id)
            buffer = ""

        while True:
            try:
                async for message in gateway.listen():
                    # If we receive any frame, connection is healthy again.
                    reconnect_delay_s = 1.0

                    request_id = _active_gateway_collation_request_id()
                    if request_id <= 0:
                        dropped_out_of_window_gateway_frames += 1
                        if dropped_out_of_window_gateway_frames <= 3 or dropped_out_of_window_gateway_frames % 50 == 0:
                            logger.info(
                                "🧩 Dropping gateway frame outside active collation window (%d dropped)",
                                dropped_out_of_window_gateway_frames,
                            )
                        continue

                    _touch_gateway_collation_window(request_id)

                    if request_id != active_buffer_request_id:
                        # New user request boundary: reset sentence buffer state.
                        buffer = ""
                        active_buffer_request_id = request_id
                        if flush_task and not flush_task.done():
                            flush_task.cancel()

                    payload_obj: dict[str, Any] | None = None
                    try:
                        parsed = json.loads(message)
                        if isinstance(parsed, dict):
                            payload_obj = parsed
                    except Exception:
                        payload_obj = None

                    if payload_obj is not None and web_service:
                        if suppress_gateway_messages_for_new_session:
                            continue
                        if startup_phase_active and isinstance(payload_obj, dict):
                            # Check if payload contains assistant text that looks like a startup greeting
                            payload_text = payload_obj.get("text") or payload_obj.get("content") or ""
                            if payload_text and is_startup_welcome_pattern(payload_text):
                                logger.info("🔇 Suppressed startup welcome pattern from gateway JSON [req#%d]: '%s'...", request_id, str(payload_text)[:60])
                                continue
                        # Fallback debug feed for providers that do not expose listen_raw().
                        if not hasattr(gateway, "listen_raw"):
                            web_service.append_chat_message(
                                {
                                    "role": "raw_gateway",
                                    "text": json.dumps(payload_obj, ensure_ascii=False),
                                    "request_id": request_id,
                                    "source": "gateway_stream",
                                }
                            )
                        step_msg, interim_msg, consumed = _extract_structured_event(payload_obj, request_id)
                        if step_msg:
                            web_service.append_chat_message(step_msg)
                        if interim_msg:
                            web_service.append_chat_message(interim_msg)
                        if consumed:
                            continue

                    text = strip_gateway_control_markers(extract_text_from_gateway_message(message))
                    if not text:
                        continue

                    if suppress_gateway_messages_for_new_session:
                        logger.info("🔇 Suppressed gateway text after new-session reset: '%s'", text[:80])
                        continue
                    
                    # During startup phase, suppress only welcome patterns
                    if startup_phase_active and is_startup_welcome_pattern(text):
                        logger.info("🔇 Suppressed startup welcome pattern: '%s'...", text[:80])
                        continue

                    # Debug: check if text starts with triple backticks
                    if text.strip().startswith('```'):
                        logger.debug("⚠️ Text starts with triple backticks: %s", text[:60])

                    _push_ui_stream_chunk(request_id, text)

                    logger.info("🔤 Received: '%s'", text)

                    # Smart concatenation: determine if space is needed
                    needs_space = False
                    if buffer:
                        # Respect explicit leading whitespace from streamed deltas.
                        if text[0].isspace():
                            needs_space = False
                        else:
                            last_char = buffer[-1]
                            first_char = text[0]
                        
                            # No space before punctuation or closing brackets
                            if first_char in ",.!?;:)]}":
                                needs_space = False
                            # No space for apostrophe-led contraction chunks (e.g., '’t', ''s')
                            elif first_char in "'’":
                                needs_space = False
                            # No space for ordinal suffix after digit (1st, 2nd, etc.)
                            elif last_char.isdigit() and len(text) >= 2 and text[:2] in ["st", "nd", "rd", "th"]:
                                needs_space = False
                            # No space between consecutive digits (for numbers like 2026)
                            elif last_char.isdigit() and first_char.isdigit():
                                needs_space = False
                            # No space after opening brackets
                            elif last_char in "([{":
                                needs_space = False
                            # No space before/after colons in times (1:28)
                            elif last_char == ":" or first_char == ":":
                                needs_space = False
                            # No space for same-word token continuation (e.g., 'Austr' + 'ia').
                            elif last_char.isalpha() and first_char.isalpha() and not text[0].isspace():
                                needs_space = False
                            else:
                                needs_space = True
                    
                    buffer += (" " if needs_space else "") + text
                    logger.info("📝 Buffer: '%s'", buffer[:100])

                    # Fast-start policy: emit first chunk once threshold words are available for this request.
                    if tts_streaming_enabled and first_chunk_word_threshold > 0 and kickoff_sent_request_id != request_id:
                        kickoff_text, remainder, boundary_safe = split_first_n_words(buffer, first_chunk_word_threshold)
                        if kickoff_text and boundary_safe and should_emit_fast_start_chunk(kickoff_text):
                            buffer = remainder
                            kickoff_sent_request_id = request_id
                            logger.info("🚀 Fast-start chunk [req#%d]: '%s'", request_id, kickoff_text)
                            await submit_tts(kickoff_text, request_id=request_id)
                            if flush_task and not flush_task.done():
                                flush_task.cancel()
                            continue

                    match = re.search(r"(.+?[.!?])\s*$", buffer)
                    if tts_streaming_enabled and match:
                        raw_sentence = match.group(1)
                        sentence = strip_gateway_control_markers(raw_sentence).strip()
                        buffer = buffer[len(raw_sentence):].strip()
                        
                        # Filter out NO_REPLY markers and other special tokens
                        if "NO_REPLY" in sentence or "NO_RE" in sentence or sentence.strip() in ["NO", "_RE", "NO _RE"]:
                            logger.info("🚫 Filtered NO_REPLY marker: '%s'", sentence)
                            if flush_task and not flush_task.done():
                                flush_task.cancel()
                            continue
                        
                        logger.info("✅ Complete sentence: '%s'", sentence)
                        await submit_tts(sentence, request_id=request_id)
                        if flush_task and not flush_task.done():
                            flush_task.cancel()
                        continue

                    if flush_task and not flush_task.done():
                        flush_task.cancel()
                    flush_task = asyncio.create_task(asyncio.sleep(flush_delay_s))
                    flush_task.add_done_callback(
                        lambda task, req_id=request_id: asyncio.create_task(flush_buffer(req_id)) if not task.cancelled() else None
                    )

                # Stream ended cleanly (e.g., websocket dropped) — reconnect.
                logger.warning("Gateway listen stream ended; reconnecting in %.1fs", reconnect_delay_s)
            except (ConnectionRefusedError, OSError) as exc:
                logger.warning("Gateway unavailable (%s); retrying listener in %.1fs", exc, reconnect_delay_s)
            except Exception as exc:
                logger.error("Gateway listener error: %s (retrying in %.1fs)", exc, reconnect_delay_s)

            if flush_task and not flush_task.done():
                flush_task.cancel()
            await asyncio.sleep(reconnect_delay_s)
            reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)

    async def gateway_raw_listener() -> None:
        reconnect_delay_s = 1.0
        reconnect_delay_max_s = 8.0

        def _should_emit_raw_debug_frame(raw_message: str) -> bool:
            """Only surface agent event frames in the raw debug bubble."""
            txt = str(raw_message or "").strip()
            if not txt:
                return False
            try:
                payload = json.loads(txt)
            except Exception:
                return False
            if not isinstance(payload, dict):
                return False
            if str(payload.get("type") or "").strip().lower() != "event":
                return False
            return str(payload.get("event") or "").strip().lower() == "agent"

        while True:
            try:
                async for raw_message in gateway.listen_raw():
                    reconnect_delay_s = 1.0
                    if not web_service:
                        continue
                    request_id = _active_gateway_collation_request_id()
                    if request_id <= 0:
                        continue
                    _touch_gateway_collation_window(request_id)
                    if suppress_gateway_messages_for_new_session:
                        continue
                    if not _should_emit_raw_debug_frame(raw_message):
                        continue

                    web_service.append_chat_message(
                        {
                            "role": "raw_gateway",
                            "text": str(raw_message),
                            "request_id": request_id,
                            "source": "gateway_stream",
                        }
                    )
            except (ConnectionRefusedError, OSError) as exc:
                logger.warning("Gateway raw listener unavailable (%s); retrying in %.1fs", exc, reconnect_delay_s)
            except Exception as exc:
                logger.error("Gateway raw listener error: %s (retrying in %.1fs)", exc, reconnect_delay_s)

            await asyncio.sleep(reconnect_delay_s)
            reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)

    async def gateway_steps_listener() -> None:
        reconnect_delay_s = 1.0
        reconnect_delay_max_s = 8.0
        while True:
            try:
                async for step in gateway.listen_steps():
                    reconnect_delay_s = 1.0
                    if not isinstance(step, dict) or not web_service:
                        continue
                    request_id = _active_gateway_collation_request_id()
                    if request_id <= 0:
                        continue
                    _touch_gateway_collation_window(request_id)
                    if suppress_gateway_messages_for_new_session:
                        continue

                    # Step events are always available when the Thinking tool timeline is visible.
                    # Emit a raw debug frame from each step so Debug JSON appears consistently.
                    web_service.append_chat_message(
                        {
                            "role": "raw_gateway",
                            "text": json.dumps(step, ensure_ascii=False),
                            "request_id": request_id,
                            "source": "gateway_steps",
                        }
                    )

                    name = str(step.get("name") or "event").strip() or "event"
                    phase = str(step.get("phase") or "update").strip() or "update"
                    details = step.get("details")
                    tool_call_id = str(step.get("toolCallId") or step.get("tool_call_id") or "").strip()

                    details_text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False)
                    if name.lower() in {"lifecycle", "compaction", "reasoning"}:
                        web_service.append_chat_message(
                            {
                                "role": "interim",
                                "text": name,
                                "phase": phase,
                                "request_id": request_id,
                                "details": details_text,
                                "source": "gateway_stream",
                            }
                        )
                    else:
                        web_service.append_chat_message(
                            {
                                "role": "step",
                                "text": name,
                                "name": name,
                                "phase": phase,
                                "request_id": request_id,
                                "tool_call_id": tool_call_id,
                                "details": details_text,
                                "source": "gateway_stream",
                            }
                        )
            except (ConnectionRefusedError, OSError) as exc:
                logger.warning("Gateway step listener unavailable (%s); retrying in %.1fs", exc, reconnect_delay_s)
            except Exception as exc:
                logger.error("Gateway step listener error: %s (retrying in %.1fs)", exc, reconnect_delay_s)

            await asyncio.sleep(reconnect_delay_s)
            reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)

    print("🎤 Audio capture starting. Press Ctrl+C to stop.", flush=True)
    logger.info("🎤 Audio capture starting. Press Ctrl+C to stop.")
    try:
        capture.start()
    except Exception as exc:
        logger.error("Audio capture failed to start with selected device (%s): %s", getattr(capture, "device", "unknown"), exc)
        if config.audio_backend != "portaudio-duplex":
            auto_cap_idx = _auto_select_audio_device(want_input=True)
            if auto_cap_idx is None:
                raise
            logger.warning(
                "Retrying capture start with auto-selected input device %s (without changing .env)",
                _describe_device(auto_cap_idx),
            )
            capture = AudioCapture(
                sample_rate=config.audio_sample_rate,
                frame_samples=frame_samples,
                device=auto_cap_idx,
                input_gain=config.audio_input_gain,
            )
            capture.start()
            logger.warning("Capture recovery succeeded using auto-selected input device for this run")
        else:
            raise
    print("🎧 Listening for audio input...\n", flush=True)
    logger.info("🎧 Listening for audio input...")
    
    # Start TTS processing loop
    asyncio.create_task(tts_loop())
    
    # Start gateway listener if supported
    if getattr(gateway, "supports_listen", False):
        asyncio.create_task(gateway_listener())
        if hasattr(gateway, "listen_raw"):
            asyncio.create_task(gateway_raw_listener())
        if hasattr(gateway, "listen_steps"):
            asyncio.create_task(gateway_steps_listener())
    
    # Start tool monitor for timers/alarms if enabled
    if timers_feature_enabled and tool_router and alert_gen:
        from orchestrator.tools.monitor import ToolMonitor
        
        # Define callbacks for timer/alarm events
        async def on_timer_expired(timer_id: str, name: str):
            """Called when a timer expires."""
            while recording_blocks_tools:
                await asyncio.sleep(0.2)
            logger.info("⏰ TIMER EXPIRED: %s (%s)", name or timer_id, timer_id)
            # Play timer bell three times before speaking completion.
            bell_pcm = alert_gen.get_timer_alert_pcm()
            bell_pcm_16k = resample_pcm(bell_pcm, alert_gen.sample_rate, config.audio_sample_rate)
            try:
                if config.audio_backend == "portaudio-duplex":
                    # For duplex, would need different handling - skip for now
                    pass
                else:
                    for ring_index in range(3):
                        await asyncio.to_thread(playback.play_pcm, bell_pcm_16k, 1.0, alarm_playback_stop_event)
                        if ring_index < 2:
                            await asyncio.sleep(0.2)
            except Exception as e:
                logger.error("Failed to play timer bell: %s", e)
            # Announce timer completion
            if name:
                await submit_tts(
                    f"Timer {name} is complete",
                    kind="notification",
                    allow_when_ui_tts_muted=True,
                )
            else:
                await submit_tts("Timer is complete", kind="notification")
        
        async def on_alarm_triggered(alarm_id: str, name: str):
            """Called when an alarm first triggers."""
            while recording_blocks_tools:
                await asyncio.sleep(0.2)
            logger.info("⏰ ALARM TRIGGERED: %s (%s)", name or alarm_id, alarm_id)
        
        async def on_alarm_ringing(alarm_id: str, name: str):
            """Called repeatedly while alarm is ringing (every few seconds)."""
            if recording_blocks_tools:
                return
            logger.info("🔔 ALARM RINGING: %s (%s)", name or alarm_id, alarm_id)
            # Play bell sound
            bell_pcm = alert_gen.get_alarm_alert_pcm()
            bell_pcm_16k = resample_pcm(bell_pcm, alert_gen.sample_rate, config.audio_sample_rate)
            try:
                if config.audio_backend == "portaudio-duplex":
                    # For duplex, would need different handling - skip for now
                    pass
                else:
                    await asyncio.to_thread(playback.play_pcm, bell_pcm_16k, 1.0, alarm_playback_stop_event)
            except Exception as e:
                logger.error("Failed to play alarm bell: %s", e)
        
        # Initialize and start tool monitor
        tool_monitor = ToolMonitor(
            timer_manager=timer_manager,
            alarm_manager=alarm_manager,
            check_interval_ms=config.tools_monitor_interval_ms,
        )
        tool_monitor.on_timer_expired = on_timer_expired
        tool_monitor.on_alarm_triggered = on_alarm_triggered
        tool_monitor.on_alarm_ringing = on_alarm_ringing
        tool_monitor.defer_processing = lambda: bool(recording_blocks_tools)
        await tool_monitor.start()
        logger.info("✓ Tool monitor started (check_interval=%dms)", config.tools_monitor_interval_ms)

    frame_count = 0
    last_heartbeat_ts = time.monotonic()
    heartbeat_interval = 10.0  # Log heartbeat every 10 seconds
    last_meter_ts = time.monotonic()
    meter_interval = 1.0
    mic_level_count = 0
    swoosh_played = False
    last_nonzero_mic_ts = time.monotonic()
    last_nonzero_sample_ts = time.monotonic()
    mic_silence_restart_s = 0.0
    hard_zero_restart_s = 6.0
    hard_zero_restarts = 0
    hard_zero_rebind_attempted = False
    mic_level_threshold = 0.001
    last_tts_speech_log_ts = 0.0
    tts_speech_log_interval = 1.0
    last_tts_meter_ts = 0.0
    tts_meter_interval = 0.5
    tts_rms_baseline = 0.0
    tts_rms_alpha = 0.05
    cut_in_hits = 0
    silero_zero_hits = 0
    alarm_cut_in_hits = 0
    alarm_shout_hits = 0
    alarm_shout_recent_hits: deque[float] = deque(maxlen=64)
    alarm_hard_stop_recent_hits: deque[float] = deque(maxlen=64)
    alarm_shout_floor = 0.0
    alarm_shout_floor_initialized = False
    last_alarm_cut_in_log_ts = 0.0
    alarm_cut_in_floor = 0.0
    alarm_cut_in_floor_initialized = False
    skip_audio_until = 0.0  # Timestamp until which audio frames are dropped (e.g. after alarm cut-in)
    speech_frame_count = 0
    min_speech_frames = max(1, int(config.vad_min_speech_ms / config.audio_frame_ms))
    
    # Music state tracking
    music_was_playing = False
    music_paused_for_wake = False
    music_auto_resume_timer = 0.0
    last_music_check_ts = 0.0
    music_check_interval = 0.5  # Check music state every 500ms
    music_tts_duck_active = False
    music_tts_duck_restore_volume: int | None = None
    music_cut_in_duck_active = False
    music_cut_in_duck_restore_volume: int | None = None
    music_cut_in_duck_until_ts = 0.0
    music_cut_in_hits = 0
    last_music_sleep_suppressed_log_ts = 0.0
    music_sleep_suppressed_log_interval_s = 8.0

    async def apply_music_duck(*, reason: str, ratio: float) -> int | None:
        """Lower music volume by ratio and return original volume for later restoration."""
        if not (config.music_enabled and music_manager):
            return None
        current = await music_manager.get_volume()
        if current is None or current < 0:
            # Music volume control is unavailable (e.g. hardware output with no mixer).
            # Skip ducking entirely so we don't accidentally restore to 0 later.
            return None
        target = max(1, min(100, int(round(current * ratio))))
        if target >= current:
            logger.debug(
                "🎚️ Skipping music duck for %s (current=%s%%, target=%s%%; no attenuation possible)",
                reason,
                current,
                target,
            )
            return None
        await music_manager.set_volume(target)
        logger.info("🎚️ Music ducked for %s: %s%% → %s%%", reason, current, target)
        return current

    async def restore_music_duck(*, reason: str, restore_volume: int | None) -> None:
        """Restore music volume after temporary ducking."""
        if not (config.music_enabled and music_manager):
            return
        if restore_volume is None:
            return
        target = max(0, min(100, int(restore_volume)))
        await music_manager.set_volume(target)
        logger.info("🎚️ Music duck restore (%s): %s%%", reason, target)

    try:
        local_capture_paused_for_browser = False
        local_capture_prev_muted = False
        browser_no_audio_logged = False
        browser_last_signal_ts: float | None = None
        browser_hybrid_using_browser = False
        browser_pcm_buffer = bytearray()
        browser_pcm_frames_count = 0
        local_mic_frames_count = 0
        last_audio_source_log_ts = time.monotonic()
        while True:
            now = time.monotonic()  # Always advance time, even when no audio frame arrives

            browser_connected = bool(web_service and web_service.has_active_client())
            browser_audio_enabled = bool(web_service and web_service._ui_control_state.get("browser_audio_enabled", True))
            continuous_mode = bool(web_service and web_service._ui_control_state.get("continuous_mode", False))
            browser_audio_ready = bool(web_service and web_service.has_recent_browser_audio(max_age_s=1.2))
            browser_level_rms = 0.0
            if web_service:
                try:
                    browser_level_rms = float(web_service.latest_browser_audio().get("rms", 0.0) or 0.0)
                except Exception:
                    browser_level_rms = 0.0
            audio_authority = str(getattr(config, "web_ui_audio_authority", "native") or "native").lower()
            if not browser_audio_enabled:
                use_browser_audio = False
            elif audio_authority == "browser":
                use_browser_audio = browser_connected and browser_audio_ready
            elif audio_authority == "hybrid":
                if browser_connected and browser_audio_ready and browser_level_rms >= max(0.001, float(config.vad_min_rms) * 0.6):
                    browser_last_signal_ts = now

                browser_signal_recent = browser_last_signal_ts is not None and (now - browser_last_signal_ts) <= 1.8
                use_browser_audio = browser_connected and browser_audio_ready and browser_signal_recent

                if use_browser_audio and not browser_hybrid_using_browser:
                    logger.info("🌐 Hybrid audio: switching to browser input (rms=%.4f)", browser_level_rms)
                    browser_hybrid_using_browser = True
                elif not use_browser_audio and browser_hybrid_using_browser:
                    logger.info("🎤 Hybrid audio: falling back to local mic (browser rms=%.4f)", browser_level_rms)
                    browser_hybrid_using_browser = False
            else:  # native (default)
                use_browser_audio = False
            if use_browser_audio:
                if not local_capture_paused_for_browser and hasattr(capture, "is_muted") and hasattr(capture, "set_muted"):
                    local_capture_prev_muted = bool(capture.is_muted())
                    capture.set_muted(True)
                    local_capture_paused_for_browser = True
                    logger.info("🌐 Browser client connected: pausing local mic stream")
                target_frame_bytes = frame_samples * 2
                if len(browser_pcm_buffer) < target_frame_bytes:
                    chunk = await web_service.read_browser_frame(timeout=1.0)
                    if chunk:
                        browser_pcm_buffer.extend(chunk)
                while len(browser_pcm_buffer) < target_frame_bytes:
                    chunk = await web_service.read_browser_frame(timeout=0.02)
                    if not chunk:
                        break
                    browser_pcm_buffer.extend(chunk)

                if len(browser_pcm_buffer) >= target_frame_bytes:
                    frame = bytes(browser_pcm_buffer[:target_frame_bytes])
                    del browser_pcm_buffer[:target_frame_bytes]
                else:
                    frame = None
                frame_source = "browser_pcm"
            else:
                if browser_connected and not browser_audio_ready and not browser_no_audio_logged:
                    logger.info("🌐 Browser client connected but no PCM frames yet; continuing to use local mic")
                    browser_no_audio_logged = True
                elif (not browser_connected or browser_audio_ready) and browser_no_audio_logged:
                    browser_no_audio_logged = False
                if local_capture_paused_for_browser and hasattr(capture, "set_muted"):
                    capture.set_muted(local_capture_prev_muted)
                    local_capture_paused_for_browser = False
                    logger.info("🌐 Browser client disconnected: resuming local mic stream")
                if browser_pcm_buffer:
                    browser_pcm_buffer.clear()
                frame = capture.read_frame(timeout=1.0)
                frame_source = "local_mic"

            if frame is None:
                # Still run period tasks so logs/heartbeat appear even when mic is silent/blocked
                if now - last_heartbeat_ts >= heartbeat_interval:
                    logger.info("💓 Heartbeat (no audio frame): state=%s", state.name)
                    last_heartbeat_ts = now
                await asyncio.sleep(0.01)
                continue

            if frame_source == "browser_pcm":
                browser_pcm_frames_count += 1
            else:
                local_mic_frames_count += 1

            if now - last_audio_source_log_ts >= 2.0:
                browser_pcm_frames_count = 0
                local_mic_frames_count = 0
                last_audio_source_log_ts = now

            frame_count += 1

            if recorder_tool and recorder_tool.is_recording():
                recorder_tool.append_frame(frame)

            # Drop audio frames during the brief post-alarm-cut-in mute window
            if skip_audio_until and now < skip_audio_until:
                continue

            if cut_in_tts_hold_active and cut_in_tts_hold_started_ts is not None:
                hold_elapsed_ms = int((now - cut_in_tts_hold_started_ts) * 1000)
                if hold_elapsed_ms >= max(0, config.vad_cut_in_tts_hold_timeout_ms):
                    cut_in_tts_hold_active = False
                    cut_in_tts_hold_started_ts = None
                    cut_in_tts_hold_request_id = 0
                    logger.info("🔓 Cut-in TTS hold timeout reached (%dms) - allowing TTS again", hold_elapsed_ms)

            processed_frame = frame
            
            # Monitor music playback state and manage orchestrator sleep during music
            if config.music_enabled and music_manager and (now - last_music_check_ts >= music_check_interval):
                try:
                    is_playing = await music_manager.is_playing()

                    # If playback has resumed (e.g., user said "play music"), clear
                    # pause-for-wake state so downstream sleep logic can run.
                    if is_playing and music_paused_for_wake:
                        music_paused_for_wake = False
                        music_auto_resume_timer = 0.0
                        logger.info("🎵 Music playback resumed → cleared wake pause state")

                    # TTS ducking while music plays (when not explicitly paused for wake/listening).
                    if config.music_tts_duck_enabled:
                        if is_playing and tts_playing and not music_paused_for_wake:
                            if not music_tts_duck_active:
                                music_tts_duck_restore_volume = await apply_music_duck(
                                    reason="tts",
                                    ratio=float(max(0.05, min(1.0, config.music_tts_duck_ratio))),
                                )
                                music_tts_duck_active = music_tts_duck_restore_volume is not None
                        elif music_tts_duck_active:
                            await restore_music_duck(
                                reason="tts complete",
                                restore_volume=music_tts_duck_restore_volume,
                            )
                            music_tts_duck_active = False
                            music_tts_duck_restore_volume = None

                    # Cut-in duck timeout (restore volume after brief attentional dip).
                    if music_cut_in_duck_active and now >= music_cut_in_duck_until_ts:
                        await restore_music_duck(
                            reason="cut-in timeout",
                            restore_volume=music_cut_in_duck_restore_volume,
                        )
                        music_cut_in_duck_active = False
                        music_cut_in_duck_restore_volume = None
                        music_cut_in_duck_until_ts = 0.0
                    
                    # Music started playing → latch playback state and (if awake)
                    # transition orchestrator to sleep.
                    if is_playing and not music_was_playing:
                        music_was_playing = True
                        music_auto_resume_timer = 0.0
                        if (
                            config.music_sleep_during_playback
                            and wake_state == WakeState.AWAKE
                            and not music_paused_for_wake
                        ):
                            logger.info("🎵 Music started → Putting orchestrator to sleep")
                            wake_state = WakeState.ASLEEP
                        else:
                            logger.info(
                                "🎵 Music start detected (wake_state=%s, paused_for_wake=%s)",
                                wake_state.value,
                                music_paused_for_wake,
                            )
                    # If we were temporarily awake (e.g., due to TTS), re-enter
                    # sleep while music is still actively playing.
                    elif (
                        is_playing
                        and music_was_playing
                        and config.music_sleep_during_playback
                        and wake_state == WakeState.AWAKE
                        and not music_paused_for_wake
                        and not tts_playing
                        and not _tts_has_pending()
                    ):
                        logger.info("🎵 Music still playing → returning orchestrator to sleep")
                        wake_state = WakeState.ASLEEP
                    
                    # Music stopped playing
                    elif not is_playing and music_was_playing:
                        music_was_playing = False
                        if music_tts_duck_active:
                            music_tts_duck_active = False
                            music_tts_duck_restore_volume = None
                        if music_cut_in_duck_active:
                            music_cut_in_duck_active = False
                            music_cut_in_duck_restore_volume = None
                            music_cut_in_duck_until_ts = 0.0
                        if music_paused_for_wake:
                            music_auto_resume_timer = 0.0
                            logger.info("🎵 Music paused for wake/listening")
                        else:
                            music_auto_resume_timer = 0.0
                            logger.info("🎵 Music stopped")
                    
                    # Handle auto-resume timer (music was paused for wake word, but no voice activity)
                    if music_paused_for_wake and not is_playing:
                        playback_state = await music_manager.get_playback_state()

                        # Only auto-resume when playback is actually paused. If user explicitly
                        # stopped playback, clear pause-for-wake state so it will not restart.
                        if playback_state != "pause":
                            logger.info(
                                "🎵 Skipping auto-resume: playback state is '%s' (not pause) → cleared wake pause state",
                                playback_state,
                            )
                            music_paused_for_wake = False
                            music_auto_resume_timer = 0.0
                        elif state in (VoiceState.IDLE, VoiceState.LISTENING):
                            # No voice activity - increment timer
                            if music_auto_resume_timer == 0.0:
                                music_auto_resume_timer = now
                            elif (now - music_auto_resume_timer) >= config.music_auto_resume_timeout_s:
                                # Timeout reached - resume music
                                logger.info("🎵 Auto-resuming music after %ds of silence", config.music_auto_resume_timeout_s)
                                await music_manager.play()
                                music_paused_for_wake = False
                                music_was_playing = True
                                music_auto_resume_timer = 0.0
                                if wake_state == WakeState.AWAKE and config.music_sleep_during_playback:
                                    wake_state = WakeState.ASLEEP
                                    logger.info("🎵 Returning orchestrator to sleep for music")
                        else:
                            # Voice activity detected - reset timer
                            music_auto_resume_timer = 0.0
                    
                except Exception as e:
                    logger.debug("Error checking music state: %s", e)
                
                last_music_check_ts = now
            
            # Periodic heartbeat to show system is alive
            if now - last_heartbeat_ts >= heartbeat_interval:
                logger.info("💓 Heartbeat: %d frames processed, state=%s", frame_count, state.name)
                last_heartbeat_ts = now

            # Live mic level meter (RMS + dBFS)
            if now - last_meter_ts >= meter_interval:
                try:
                    samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                    if samples.size:
                        rms = float(np.sqrt(np.mean(samples ** 2)) / 32768.0)
                        dbfs = 20.0 * math.log10(max(rms, 1e-6))
                        source_label = "Browser" if use_browser_audio else "Mic"
                        if use_browser_audio and web_service:
                            browser_level = web_service.latest_browser_audio()
                            logger.log(
                                AUDIO_LOG_LEVEL,
                                "Mic level: frame_rms=%.4f (%.1f dBFS), browser_rms=%.4f, browser_peak=%.4f",
                                rms,
                                dbfs,
                                float(browser_level.get("rms", 0.0) or 0.0),
                                float(browser_level.get("peak", 0.0) or 0.0),
                            )
                        else:
                            logger.log(AUDIO_LOG_LEVEL, "Mic level: %.4f (%.1f dBFS)", rms, dbfs)
                        mic_level_count += 1
                        
                        # Swoosh sound disabled
                        # (Was: Play swoosh after second mic level log, but disabled per user request)
                        if False:  # DISABLED
                            try:
                                swoosh_sound = generate_swoosh_sound(sample_rate=config.audio_sample_rate)
                                playback.play_pcm(swoosh_sound, gain=2.5, stop_event=threading.Event())
                                logger.info("✓ Readiness chime (swoosh) played")
                                swoosh_played = True
                            except Exception as e:
                                logger.debug("Failed to play readiness chime: %s", e)
                                swoosh_played = True
                        else:
                            swoosh_played = True  # Mark as played so we don't keep trying
                        
                        if rms > mic_level_threshold:
                            last_nonzero_mic_ts = now
                except Exception as exc:  # pragma: no cover
                    logger.warning("Mic level meter error: %s", exc)
                last_meter_ts = now

            if mic_silence_restart_s > 0 and not tts_playing and (now - last_nonzero_mic_ts) >= mic_silence_restart_s:
                logger.warning("Mic silent for %.1fs → restarting capture", mic_silence_restart_s)
                try:
                    capture.restart()
                except Exception as exc:  # pragma: no cover
                    logger.warning("Audio capture restart failed: %s", exc)
                last_nonzero_mic_ts = now

            # Calculate RMS from RAW frame (before AEC) for diagnostics
            rms_raw = 0.0
            try:
                raw_samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                if raw_samples.size:
                    if np.any(raw_samples != 0.0):
                        last_nonzero_sample_ts = now
                    rms_raw = float(np.sqrt(np.mean(raw_samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms_raw = 0.0

            # Recovery for stale/invalid capture handles that return digital silence forever
            # (common after USB replug or PipeWire source churn): restart, then rebind once.
            if (
                not use_browser_audio
                and not tts_playing
                and (now - last_nonzero_sample_ts) >= hard_zero_restart_s
            ):
                logger.warning("Mic frames all-zero for %.1fs → restarting capture", hard_zero_restart_s)
                try:
                    capture.restart()
                    hard_zero_restarts += 1
                except Exception as exc:  # pragma: no cover
                    logger.warning("Audio capture restart (all-zero recovery) failed: %s", exc)
                last_nonzero_sample_ts = now

                if (
                    hard_zero_restarts >= 2
                    and not hard_zero_rebind_attempted
                    and config.audio_backend != "portaudio-duplex"
                ):
                    configured_capture = str(config.audio_capture_device).strip().lower()
                    stay_pipewire_shared = configured_capture in {"pipewire", "default"}
                    if stay_pipewire_shared:
                        hard_zero_rebind_attempted = True
                        logger.warning(
                            "Mic still all-zero after restarts, but capture device is '%s' → keeping PipeWire shared mode (no direct ALSA rebind)",
                            config.audio_capture_device,
                        )
                        continue

                    hard_zero_rebind_attempted = True
                    auto_cap_idx = _auto_select_physical_input_device(preferred_rate=config.audio_sample_rate)
                    if auto_cap_idx is None:
                        auto_cap_idx = _auto_select_audio_device(want_input=True)
                    if auto_cap_idx is not None:
                        logger.warning(
                            "Mic still all-zero after restarts → rebinding capture to auto-selected input %s",
                            _describe_device(auto_cap_idx),
                        )
                        try:
                            candidate_capture = AudioCapture(
                                sample_rate=config.audio_sample_rate,
                                frame_samples=frame_samples,
                                device=auto_cap_idx,
                                input_gain=config.audio_input_gain,
                            )
                            candidate_capture.start()
                        except Exception as exc:
                            logger.warning(
                                "Auto-rebind candidate failed (%s): %s; keeping existing capture",
                                _describe_device(auto_cap_idx),
                                exc,
                            )
                        else:
                            try:
                                capture.stop()
                            except Exception:
                                pass
                            capture = candidate_capture
                            hard_zero_restarts = 0
                            last_nonzero_sample_ts = now
                            logger.warning("Capture rebind recovery succeeded; monitoring mic levels")

            _alarm_ringing = bool(alarm_manager and alarm_manager.ringing_alarms)
            if aec and (tts_playing or _alarm_ringing) and last_playback_frame:
                try:
                    processed_frame = aec.process(frame, last_playback_frame)
                except NotImplementedError:
                    processed_frame = frame
                    if not warned_aec_stub:
                        logger.warning("WebRTC AEC bindings not configured; passing mic audio through.")
                        warned_aec_stub = True

            # Calculate RMS from processed frame (after AEC) for cut-in detection
            rms_cutin = 0.0
            try:
                cutin_samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                if cutin_samples.size:
                    rms_cutin = float(np.sqrt(np.mean(cutin_samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms_cutin = 0.0

            # Track baseline RMS during TTS playback to detect mic speech over speaker bleed
            if tts_playing:
                if tts_rms_baseline == 0.0:
                    tts_rms_baseline = rms_raw
                else:
                    tts_rms_baseline = (1.0 - tts_rms_alpha) * tts_rms_baseline + tts_rms_alpha * rms_raw
            else:
                tts_rms_baseline = 0.0
                cut_in_hits = 0
                silero_zero_hits = 0

            # Store raw frame in ring buffer (not AEC-processed)
            # AEC is too aggressive during TTS and removes user speech along with echo
            # We use RMS baseline tracking for echo rejection instead
            ring_buffer.add_frame(frame)

            recorder_hotword_mode = bool(
                recorder_tool
                and recorder_tool.is_recording()
                and wake_detector is not None
            )

            # Emergency shout-to-stop path for ringing alarms.
            # Uses raw mic RMS only so it still works when VAD/AEC miss speech under loud bell playback.
            if alarm_manager and alarm_manager.ringing_alarms and not tts_playing:
                now_wall = time.time()
                _ring_ages = []
                for _rid in list(alarm_manager.ringing_alarms):
                    _ra = alarm_manager.get_alarm(_rid)
                    if _ra and _ra.triggered_at is not None:
                        _ring_ages.append(now_wall - float(_ra.triggered_at))
                _min_ring_age_for_shout = min(_ring_ages) if _ring_ages else 999.0
                _shout_min_ring_age_s = max(1.0, float(config.alarm_cut_in_arming_s))
                _hard_stop_min_ring_age_s = max(2.5, _shout_min_ring_age_s + 1.0)
                _shout_rms_threshold = max(0.005, float(config.alarm_shout_rms))
                _shout_required_frames = int(config.alarm_shout_frames)
                _hard_stop_rms_threshold = max(0.020, _shout_rms_threshold * 1.2)
                _hard_stop_extreme_rms = max(0.050, _shout_rms_threshold * 3.0)
                if not alarm_shout_floor_initialized:
                    alarm_shout_floor = rms_raw
                    alarm_shout_floor_initialized = True
                else:
                    alarm_shout_floor = (0.96 * alarm_shout_floor) + (0.04 * rms_raw)
                _shout_excess = max(0.0, rms_raw - alarm_shout_floor)
                _shout_excess_threshold = max(0.0035, _shout_rms_threshold * 0.22)
                if _shout_required_frames > 0 and _min_ring_age_for_shout >= _shout_min_ring_age_s:
                    _shout_frame = (rms_raw >= _shout_rms_threshold) and (_shout_excess >= _shout_excess_threshold)
                    _extreme_shout_frame = (rms_raw >= max(0.03, _shout_rms_threshold * 2.5)) and (
                        _shout_excess >= max(0.008, _shout_excess_threshold * 1.6)
                    )
                    _hard_stop_frame = rms_raw >= _hard_stop_rms_threshold
                    _hard_stop_extreme_frame = rms_raw >= _hard_stop_extreme_rms
                    if _shout_frame:
                        alarm_shout_hits += 1
                        alarm_shout_recent_hits.append(now)
                    else:
                        alarm_shout_hits = 0
                    if _hard_stop_frame and _min_ring_age_for_shout >= _hard_stop_min_ring_age_s:
                        alarm_hard_stop_recent_hits.append(now)
                    _shout_window_s = 1.2
                    while alarm_shout_recent_hits and (now - alarm_shout_recent_hits[0]) > _shout_window_s:
                        alarm_shout_recent_hits.popleft()
                    while alarm_hard_stop_recent_hits and (now - alarm_hard_stop_recent_hits[0]) > _shout_window_s:
                        alarm_hard_stop_recent_hits.popleft()
                    _shout_window_hits = len(alarm_shout_recent_hits)
                    _hard_stop_window_hits = len(alarm_hard_stop_recent_hits)
                    if (
                        (_hard_stop_extreme_frame and _min_ring_age_for_shout >= _hard_stop_min_ring_age_s)
                        or _hard_stop_window_hits >= 3
                    ):
                        logger.info(
                            "✋ Alarm hard-stop triggered (rms_raw=%.4f, hard_hits=%d, extreme=%s, threshold=%.4f, min_age=%.2f, age=%.2f)",
                            rms_raw,
                            _hard_stop_window_hits,
                            _hard_stop_extreme_frame,
                            _hard_stop_rms_threshold,
                            _hard_stop_min_ring_age_s,
                            _min_ring_age_for_shout,
                        )
                        dismissed_hard_stop = await stop_ringing_alarms_immediately("voice hard-stop fallback")
                        if dismissed_hard_stop > 0:
                            alarm_shout_hits = 0
                            alarm_shout_recent_hits.clear()
                            alarm_hard_stop_recent_hits.clear()
                            alarm_shout_floor = 0.0
                            alarm_shout_floor_initialized = False
                            alarm_cut_in_hits = 0
                            alarm_cut_in_floor = 0.0
                            alarm_cut_in_floor_initialized = False
                            chunk_frames = []
                            chunk_start_ts = None
                            last_speech_ts = None
                            cut_in_triggered_ts = None
                            ring_buffer.clear()
                            state = VoiceState.IDLE
                            skip_audio_until = now + 0.75
                            logger.info("✋ Alarm dismissed via hard raw-audio fallback")
                            continue
                    if _extreme_shout_frame or _shout_window_hits >= _shout_required_frames:
                        logger.info(
                            "✋ Alarm emergency shout-stop triggered (rms_raw=%.4f, floor=%.4f, excess=%.4f, hits=%d/%d, window_hits=%d, extreme=%s, threshold=%.4f)",
                            rms_raw,
                            alarm_shout_floor,
                            _shout_excess,
                            alarm_shout_hits,
                            _shout_required_frames,
                            _shout_window_hits,
                            _extreme_shout_frame,
                            _shout_rms_threshold,
                        )
                        dismissed_shout = await stop_ringing_alarms_immediately("voice shout override")
                        if dismissed_shout > 0:
                            alarm_shout_hits = 0
                            alarm_shout_recent_hits.clear()
                            alarm_hard_stop_recent_hits.clear()
                            alarm_shout_floor = 0.0
                            alarm_shout_floor_initialized = False
                            alarm_cut_in_hits = 0
                            alarm_cut_in_floor = 0.0
                            alarm_cut_in_floor_initialized = False
                            chunk_frames = []
                            chunk_start_ts = None
                            last_speech_ts = None
                            cut_in_triggered_ts = None
                            ring_buffer.clear()
                            state = VoiceState.IDLE
                            skip_audio_until = now + 0.75
                            logger.info("✋ Alarm dismissed via emergency shout override")
                            continue
                else:
                    alarm_shout_hits = 0
                    alarm_shout_recent_hits.clear()
                    alarm_hard_stop_recent_hits.clear()
            else:
                alarm_shout_hits = 0
                alarm_shout_recent_hits.clear()
                alarm_hard_stop_recent_hits.clear()
                alarm_shout_floor = 0.0
                alarm_shout_floor_initialized = False

            # Alarm cut-in detection for ASLEEP state: the wake-word ASLEEP block below
            # always `continue`s, which would skip the main alarm cut-in check entirely.
            # This early check handles the case where the system is asleep when an alarm
            # rings and the user speaks — without needing a wakeword to stop the alarm.
            if alarm_manager and alarm_manager.ringing_alarms and not tts_playing and wake_state == WakeState.ASLEEP and config.alarm_audio_stop_enabled:
                now_wall = time.time()
                _ring_ages = []
                for _rid in list(alarm_manager.ringing_alarms):
                    _ra = alarm_manager.get_alarm(_rid)
                    if _ra and _ra.triggered_at is not None:
                        _ring_ages.append(now_wall - float(_ra.triggered_at))
                _min_ring_age = min(_ring_ages) if _ring_ages else 999.0
                _alarm_cutin_arming_s = max(0.0, float(config.alarm_cut_in_arming_s))
                if _min_ring_age < _alarm_cutin_arming_s:
                    alarm_cut_in_hits = 0
                else:
                    _speech_like_min_ring_age_s = max(1.2, _alarm_cutin_arming_s + 0.35)
                    _asig = max(rms_raw, rms_cutin)
                    _athr = min(config.vad_cut_in_rms, max(config.vad_min_rms * 1.25, 0.0018))
                    if not alarm_cut_in_floor_initialized:
                        alarm_cut_in_floor = _asig
                        alarm_cut_in_floor_initialized = True
                    else:
                        alarm_cut_in_floor = 0.92 * alarm_cut_in_floor + 0.08 * _asig
                    _aexcess = max(0.0, _asig - alarm_cut_in_floor)
                    # Use both raw and AEC-processed VAD paths. AEC can attenuate user
                    # speech while the alarm is playing; raw VAD is more reliable for
                    # "shout to stop alarm" behavior.
                    _raw_vad_frame = frame
                    _processed_vad_frame = processed_frame
                    if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                        _raw_vad_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                        _processed_vad_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
                    _asleep_vad_raw = vad.is_speech(_raw_vad_frame)
                    _asleep_vad_processed = vad.is_speech(_processed_vad_frame)
                    _asleep_silero_gate = True
                    _asleep_silero_conf: float | None = None
                    if config.vad_cut_in_use_silero and cut_in_silero is not None:
                        _silero_alarm_frame = frame
                        if config.audio_sample_rate != 16000:
                            _silero_alarm_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                        _silero_alarm_result = cut_in_silero.is_speech(_silero_alarm_frame)
                        _asleep_silero_conf = _silero_alarm_result.confidence
                        _asleep_silero_gate = _asleep_silero_conf >= config.vad_cut_in_silero_confidence

                    _vad_speech_like = bool(_asleep_vad_raw.speech_detected or _asleep_vad_processed.speech_detected)
                    _rms_or_excess = (_asig >= _athr) or (_aexcess >= max(0.0010, _athr * 0.25))
                    _alarm_speech_like_candidate = (
                        _vad_speech_like
                        and _rms_or_excess
                        and (_aexcess >= max(0.0030, _athr * 0.55))
                        and (_min_ring_age >= _speech_like_min_ring_age_s)
                    )
                    if _alarm_speech_like_candidate:
                        alarm_cut_in_hits += 1
                    else:
                        alarm_cut_in_hits = 0
                    _required_hits = max(2, int(config.alarm_cut_in_required_hits))
                    if alarm_cut_in_hits >= _required_hits:
                        if now - last_alarm_cut_in_log_ts >= tts_speech_log_interval:
                            logger.info(
                                "✋ Alarm speech-like stop triggered (asleep)! (rms_raw=%.4f, rms_aec=%.4f, floor=%.4f, excess=%.4f, vad_raw=%s, vad_aec=%s, silero_gate=%s, silero_conf=%s, candidate=%s, hits=%d/%d)",
                                rms_raw, rms_cutin, alarm_cut_in_floor, _aexcess,
                                _asleep_vad_raw.speech_detected,
                                _asleep_vad_processed.speech_detected,
                                _asleep_silero_gate,
                                f"{_asleep_silero_conf:.3f}" if _asleep_silero_conf is not None else "n/a",
                                _alarm_speech_like_candidate,
                                alarm_cut_in_hits, _required_hits,
                            )
                            print("✋ Alarm speech-like stop triggered (asleep) → stopping alarm", flush=True)
                            last_alarm_cut_in_log_ts = now
                        dismissed_asleep = await stop_ringing_alarms_immediately("voice any sound (asleep)")
                        if dismissed_asleep > 0:
                            logger.info("✋ Voice cut-in (asleep) dismissed active alarm ringing")
                            alarm_cut_in_hits = 0
                            alarm_cut_in_floor = 0.0
                            alarm_cut_in_floor_initialized = False
                            chunk_frames = []
                            chunk_start_ts = None
                            last_speech_ts = None
                            cut_in_triggered_ts = None
                            ring_buffer.clear()
                            state = VoiceState.IDLE
                            skip_audio_until = now + 0.75
                            logger.info("✋ Dropping audio for 750ms to suppress alarm-cut-in transcript")

            alarm_ringing_now = bool(alarm_manager and alarm_manager.ringing_alarms)
            if (recorder_hotword_mode or (config.wake_word_enabled and (not continuous_mode))) and wake_state == WakeState.ASLEEP and not alarm_ringing_now:
                if web_service:
                    web_service.update_orchestrator_status(
                        voice_state=state.value,
                        wake_state=wake_state.value,
                        speech_active=bool(chunk_frames),
                        tts_playing=tts_playing,
                        mic_rms=rms_raw,
                        queue_depth=len(tts_queue),
                        wake_word_enabled=config.wake_word_enabled,
                    )

                # While sleeping during music playback, use voice cut-in only to duck
                # volume briefly so the hotword can be heard more reliably.
                if (
                    config.music_enabled
                    and config.music_sleep_during_playback
                    and music_manager
                    and music_was_playing
                    and not tts_playing
                ):
                    music_cutin_vad_frame = processed_frame
                    if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                        music_cutin_vad_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
                    music_cutin_vad_result = vad.is_speech(music_cutin_vad_frame)
                    cut_in_voice_candidate = bool(music_cutin_vad_result.speech_detected) and rms_raw >= config.vad_cut_in_rms
                    if cut_in_voice_candidate:
                        music_cut_in_hits += 1
                    else:
                        music_cut_in_hits = 0

                    if music_cut_in_hits >= config.vad_cut_in_frames:
                        music_cut_in_hits = 0

                        if not music_cut_in_duck_active:
                            music_cut_in_duck_restore_volume = await apply_music_duck(
                                reason="voice cut-in",
                                ratio=float(max(0.05, min(1.0, config.music_cut_in_duck_ratio))),
                            )
                            music_cut_in_duck_active = music_cut_in_duck_restore_volume is not None
                            if music_cut_in_duck_active:
                                music_cut_in_duck_until_ts = now + (max(0, config.music_cut_in_duck_timeout_ms) / 1000.0)
                                logger.info(
                                    "🎚️ Voice cut-in while music sleeping → ducking for hotword window (%dms)",
                                    max(0, config.music_cut_in_duck_timeout_ms),
                                )

                # Keep awake while TTS is playing or queued so cut-in can work
                # But do NOT wake up solely due to TTS when sleeping because music
                # playback is active; music-sleep must remain authoritative.
                keep_sleep_for_music = (
                    config.music_enabled
                    and config.music_sleep_during_playback
                    and music_was_playing
                    and not music_paused_for_wake
                )
                if keep_sleep_for_music and (now - last_music_sleep_suppressed_log_ts) >= music_sleep_suppressed_log_interval_s:
                    logger.info(
                        "😴 Music sleep active: suppressing wake/listen processing while playback is active"
                    )
                    last_music_sleep_suppressed_log_ts = now
                if (tts_playing or _tts_has_pending()) and not keep_sleep_for_music:
                    wake_state = WakeState.AWAKE
                    last_activity_ts = now
                else:
                    if wake_sleep_ts is not None and (now - wake_sleep_ts) * 1000 < wake_sleep_cooldown_ms:
                        await asyncio.sleep(0)
                        continue
                    if wake_detector:
                        wake_frame = processed_frame
                        # DEBUG: Check frame properties
                        frame_len = len(wake_frame) if wake_frame else 0
                        frame_rms = 0.0
                        if frame_len > 0:
                            samples = np.frombuffer(wake_frame, dtype=np.int16)
                            # Avoid RuntimeWarning by ensuring positive values
                            mean_sq = np.mean(samples.astype(np.float64) ** 2)
                            frame_rms = float(np.sqrt(max(mean_sq, 0.0)) / 32768.0)
                        
                        if config.audio_sample_rate != 16000:
                            if not warned_wake_resample:
                                logger.warning("Wake word expects 16kHz audio; resampling from %s Hz.", config.audio_sample_rate)
                                warned_wake_resample = True
                            wake_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
                        
                        try:
                            wake_result = wake_detector.detect(wake_frame)
                        except Exception as e:
                            logger.error("Wake detector error: %s", e)
                            wake_result = WakeWordResult(detected=False, confidence=0.0)

                        # Get active confidence threshold for logging
                        if active_wake_engine == "openwakeword":
                            active_confidence = config.openwakeword_confidence
                        elif active_wake_engine == "precise":
                            active_confidence = config.precise_confidence
                        elif active_wake_engine == "picovoice":
                            active_confidence = config.picovoice_confidence
                        else:
                            active_confidence = 0.5
                        
                        # Log only meaningful confidence spikes and rate-limit to avoid log spam.
                        wake_conf_log_threshold = max(0.15, active_confidence * 0.5)
                        if wake_result.confidence >= wake_conf_log_threshold and (now - last_wake_conf_log_ts) >= 0.75:
                            logger.info("Wake confidence spike: %.4f (frame_rms=%.6f, frame_len=%d)", wake_result.confidence, frame_rms, frame_len)
                            last_wake_conf_log_ts = now
                        elif frame_rms > 0.05 and (now - last_wake_conf_log_ts) >= 1.0:  # Also log when there's significant audio
                            logger.info("Audio detected but no spike: conf=%.4f, frame_rms=%.6f", wake_result.confidence, frame_rms)

                        if wake_result.detected:
                            # Guard against false detections on silence right after timeout/sleep.
                            if frame_rms < config.wake_min_detect_rms:
                                logger.info(
                                    "Ignoring wake detection on low RMS frame (conf=%.4f, rms=%.6f < %.6f)",
                                    wake_result.confidence,
                                    frame_rms,
                                    config.wake_min_detect_rms,
                                )
                                if wake_detector and hasattr(wake_detector, 'reset_state'):
                                    wake_detector.reset_state()
                                await asyncio.sleep(0)
                                continue
                            logger.info(
                                "🔥 Hotword trigger detected (mode=%s, conf=%.4f, rms=%.4f, engine=%s)",
                                "recording" if recorder_hotword_mode else "normal",
                                wake_result.confidence,
                                frame_rms,
                                active_wake_engine or "unknown",
                            )

                            if recorder_hotword_mode and recorder_tool and recorder_tool.is_recording():
                                if web_service:
                                    web_service.note_hotword_detected()
                                recorder_stop_hotword_armed_ts = now
                                wake_state = WakeState.AWAKE
                                wake_sleep_ts = None
                                last_wake_detected_ts = now
                                last_activity_ts = now
                                state = VoiceState.LISTENING
                                logger.info("🎙️ Recorder stop phrase armed by hotword; waiting for spoken stop command")
                                await asyncio.sleep(0)
                                continue

                            wake_state = WakeState.AWAKE
                            wake_sleep_ts = None
                            last_wake_detected_ts = now
                            if web_service:
                                web_service.note_hotword_detected()
                                web_service.update_ui_control_state(mic_enabled=True)
                            last_activity_ts = now
                            state = VoiceState.LISTENING
                            chunk_start_ts = now

                            # If we temporarily ducked music for hotword assist,
                            # restore normal level before pausing so resume returns
                            # to the expected user volume.
                            if music_cut_in_duck_active:
                                await restore_music_duck(
                                    reason="hotword detected",
                                    restore_volume=music_cut_in_duck_restore_volume,
                                )
                                music_cut_in_duck_active = False
                                music_cut_in_duck_restore_volume = None
                                music_cut_in_duck_until_ts = 0.0
                            
                            # Stop music playback when wake word detected
                            if config.music_enabled and music_manager:
                                try:
                                    is_playing = await music_manager.is_playing()
                                    if is_playing:
                                        logger.info("🎵 Pausing music for wake word")
                                        await music_manager.pause()
                                        music_paused_for_wake = True
                                        music_auto_resume_timer = 0.0
                                except Exception as e:
                                    logger.debug("Error stopping music: %s", e)
                            
                            # Clear ring buffer if configured to avoid stale pre-wake audio (prevents ghost transcripts)
                            # Recommended for ARM systems where ring buffer latency is high
                            if config.wake_clear_ring_buffer:
                                ring_buffer.clear()
                                chunk_frames = [frame]
                            else:
                                # Reduced prebuffer from 200ms to 80ms to avoid capturing the hotword itself being spoken
                                wake_pre_roll_ms = min(80, config.pre_roll_ms)
                                wake_pre_roll_frames = max(0, int(wake_pre_roll_ms / config.audio_frame_ms))
                                prebuffer = ring_buffer.get_frames()
                                if wake_pre_roll_frames > 0 and len(prebuffer) > wake_pre_roll_frames:
                                    prebuffer = prebuffer[-wake_pre_roll_frames:]
                                chunk_frames = prebuffer
                                chunk_frames.append(frame)
                            last_speech_ts = now
                            logger.info(
                                "🎙️ Wake word detected → awake (conf=%.4f, rms=%.4f, engine=%s) | timeout=%dms | listening | mic_btn=green",
                                wake_result.confidence, frame_rms, active_wake_engine or "unknown",
                                config.wake_word_timeout_ms,
                            )
                            if wake_click_sound:
                                try:
                                    play_feedback_async(
                                        wake_click_sound,
                                        float(max(0.1, config.wake_feedback_gain)),
                                        "wake click (hotword)",
                                    )
                                except Exception as exc:
                                    logger.debug("Failed to play wake click sound: %s", exc)
                    await asyncio.sleep(0)
                    continue

            vad_frame = processed_frame
            if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                vad_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
            vad_result = vad.is_speech(vad_frame)
            vad_result_cutin = vad_result
            if tts_playing:
                # Use raw frame for cut-in VAD (AEC removes user speech along with echo)
                # RMS baseline tracking handles echo rejection instead
                vad_cutin_frame = frame
                if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                    vad_cutin_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                vad_result_cutin = vad.is_speech(vad_cutin_frame)
            silero_gate = True
            silero_conf = None
            if tts_playing and config.vad_cut_in_use_silero:
                silero_gate = False
                if cut_in_silero is not None:
                    # Use raw frame for Silero (it should distinguish speech from echo better than WebRTC AEC)
                    silero_frame = frame
                    if config.audio_sample_rate != 16000:
                        silero_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                    silero_result = cut_in_silero.is_speech(silero_frame)
                    silero_conf = silero_result.confidence
                    silero_gate = silero_conf >= config.vad_cut_in_silero_confidence
            rms = 0.0
            try:
                samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                if samples.size:
                    rms = float(np.sqrt(np.mean(samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms = 0.0

            speech_hit = bool(vad_result.speech_detected) and rms >= config.vad_min_rms
            if speech_hit:
                speech_frame_count += 1
                # Adjust microphone gain based on speech RMS if enabled and device is not excluded
                if mic_volume_adjuster.should_process_device(config.audio_capture_device):
                    new_gain, mic_msg = mic_volume_adjuster.adjust_gain(rms, now)
                    if mic_msg:
                        logger.info(mic_msg)
                    # Apply the adjusted gain to the frame if it has changed
                    if abs(new_gain - 1.0) > 0.001:
                        try:
                            # Apply gain adjustment to samples
                            adjusted_samples = (samples * new_gain).astype(np.int16)
                            # Clamp to valid int16 range
                            adjusted_samples = np.clip(adjusted_samples, -32768, 32767)
                            frame = adjusted_samples.astype(np.int16).tobytes()
                            processed_frame = frame  # Update for downstream processing
                            # Recalculate RMS with adjusted frame
                            rms = float(np.sqrt(np.mean(adjusted_samples.astype(np.float32) ** 2)) / 32768.0)
                        except Exception as exc:
                            logger.debug("Failed to apply mic gain adjustment: %s", exc)
            else:
                speech_frame_count = 0

            if web_service:
                web_service.update_orchestrator_status(
                    voice_state=state.value,
                    wake_state=wake_state.value,
                    speech_active=bool(speech_hit or chunk_frames),
                    tts_playing=tts_playing,
                    mic_rms=rms_raw,
                    queue_depth=len(tts_queue),
                    wake_word_enabled=config.wake_word_enabled,
                )

            if tts_playing:
                # During TTS playback, suppress the normal speech→STT pipeline entirely
                # to prevent TTS echo from feeding back as transcripts and causing a
                # response loop. The cut-in path is the only authorised route to start
                # accumulating audio while TTS is active.
                if chunk_frames and cut_in_triggered_ts is None:
                    # TTS started while a chunk was already in progress (race condition).
                    # Discard the stale pre-TTS audio to avoid sending it as a new utterance.
                    logger.debug(
                        "TTS active: discarding stale in-progress chunk (%d frames, ~%dms)",
                        len(chunk_frames),
                        len(chunk_frames) * config.audio_frame_ms,
                    )
                    chunk_frames = []
                    chunk_start_ts = None
                    last_speech_ts = None
                    cut_in_triggered_ts = None
                    ring_buffer.clear()
                    if state == VoiceState.LISTENING:
                        state = VoiceState.IDLE
                elif chunk_frames and cut_in_triggered_ts is not None:
                    # Cut-in chunk in progress: keep appending frames and advance
                    # last_speech_ts so silence detection times out correctly.
                    chunk_frames.append(processed_frame)
                    if speech_frame_count >= min_speech_frames:
                        last_speech_ts = now
                        last_activity_ts = now
                # else: no chunk and no cut-in → nothing to do; cut-in detection loop
                # will initiate chunk_frames when a genuine voice cut-in is confirmed.
            else:
                if speech_frame_count >= min_speech_frames:
                    last_activity_ts = now
                    last_speech_ts = now
                    if not chunk_frames:
                        chunk_start_ts = now
                        chunk_frames = ring_buffer.get_frames()
                    chunk_frames.append(processed_frame)
                    if state == VoiceState.IDLE:
                        state = VoiceState.LISTENING
                        print("🎤 Speech detected → listening for transcription", flush=True)
                        logger.info(
                            "🎤 Speech detected → listening for transcription | wake=%s",
                            wake_state.value,
                        )
                elif chunk_frames:
                    chunk_frames.append(processed_frame)

            alarm_ringing_active = bool(alarm_manager and alarm_manager.ringing_alarms)
            if alarm_ringing_active and not tts_playing and config.alarm_audio_stop_enabled:
                now_wall = time.time()
                ring_ages = []
                if alarm_manager:
                    for rid in list(alarm_manager.ringing_alarms):
                        ra = alarm_manager.get_alarm(rid)
                        if ra and ra.triggered_at is not None:
                            ring_ages.append(now_wall - float(ra.triggered_at))
                min_ring_age = min(ring_ages) if ring_ages else 999.0
                alarm_cutin_arming_s = max(0.0, float(config.alarm_cut_in_arming_s))
                if min_ring_age < alarm_cutin_arming_s:
                    alarm_cut_in_hits = 0
                else:
                    alarm_speech_like_min_ring_age_s = max(1.2, alarm_cutin_arming_s + 0.35)
                    alarm_signal = max(rms_raw, rms_cutin)
                    if not alarm_cut_in_floor_initialized:
                        alarm_cut_in_floor = alarm_signal
                        alarm_cut_in_floor_initialized = True
                    else:
                        alarm_cut_in_floor = (0.92 * alarm_cut_in_floor) + (0.08 * alarm_signal)

                    alarm_cut_in_threshold = min(config.vad_cut_in_rms, max(config.vad_min_rms * 1.25, 0.0018))
                    alarm_rms_excess = max(0.0, alarm_signal - alarm_cut_in_floor)

                    alarm_vad_raw_frame = frame
                    alarm_vad_processed_frame = processed_frame
                    if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                        alarm_vad_raw_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                        alarm_vad_processed_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
                    alarm_vad_raw = vad.is_speech(alarm_vad_raw_frame)
                    alarm_vad_processed = vad.is_speech(alarm_vad_processed_frame)
                    alarm_silero_gate = True
                    alarm_silero_conf: float | None = None
                    if config.vad_cut_in_use_silero and cut_in_silero is not None:
                        alarm_silero_frame = frame
                        if config.audio_sample_rate != 16000:
                            alarm_silero_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                        alarm_silero_result = cut_in_silero.is_speech(alarm_silero_frame)
                        alarm_silero_conf = alarm_silero_result.confidence
                        alarm_silero_gate = alarm_silero_conf >= config.vad_cut_in_silero_confidence
                    alarm_vad_speech_like = bool(alarm_vad_raw.speech_detected or alarm_vad_processed.speech_detected)
                    alarm_rms_or_excess = (
                        (rms_raw >= alarm_cut_in_threshold)
                        or (rms_cutin >= alarm_cut_in_threshold)
                        or (alarm_rms_excess >= max(0.0010, alarm_cut_in_threshold * 0.25))
                    )
                    alarm_cut_in_candidate = (
                        alarm_vad_speech_like
                        and alarm_rms_or_excess
                        and (alarm_rms_excess >= max(0.0030, alarm_cut_in_threshold * 0.55))
                        and (min_ring_age >= alarm_speech_like_min_ring_age_s)
                    )
                    if alarm_cut_in_candidate:
                        alarm_cut_in_hits += 1
                    else:
                        alarm_cut_in_hits = 0

                    alarm_cut_in_required_hits = max(2, int(config.alarm_cut_in_required_hits))
                    alarm_cut_in = alarm_cut_in_hits >= alarm_cut_in_required_hits
                    if alarm_cut_in and now - last_alarm_cut_in_log_ts >= tts_speech_log_interval:
                        logger.info(
                            "✋ Alarm speech-like stop triggered! (rms_raw=%.4f, rms_aec=%.4f, floor=%.4f, excess=%.4f, vad_raw=%s, vad_aec=%s, silero_gate=%s, silero_conf=%s, candidate=%s, hits=%d/%d)",
                            rms_raw,
                            rms_cutin,
                            alarm_cut_in_floor,
                            alarm_rms_excess,
                            alarm_vad_raw.speech_detected,
                            alarm_vad_processed.speech_detected,
                            alarm_silero_gate,
                            f"{alarm_silero_conf:.3f}" if alarm_silero_conf is not None else "n/a",
                            alarm_cut_in_candidate,
                            alarm_cut_in_hits,
                            alarm_cut_in_required_hits,
                        )
                        print("✋ Alarm speech-like stop triggered → stopping alarm", flush=True)
                        last_alarm_cut_in_log_ts = now

                    if alarm_cut_in:
                        dismissed_by_cut_in = await stop_ringing_alarms_immediately("voice any sound")
                        if dismissed_by_cut_in > 0:
                            logger.info("✋ Voice cut-in dismissed active alarm ringing")
                            alarm_cut_in_hits = 0
                            chunk_frames = []
                            chunk_start_ts = None
                            last_speech_ts = None
                            cut_in_triggered_ts = None
                            ring_buffer.clear()
                            state = VoiceState.IDLE
                            skip_audio_until = now + 0.75
                            logger.info("✋ Dropping audio for 750ms to suppress alarm-cut-in transcript")
            else:
                alarm_cut_in_hits = 0
                alarm_cut_in_floor = 0.0
                alarm_cut_in_floor_initialized = False

            if tts_playing:
                if now - last_tts_meter_ts >= tts_meter_interval:
                    logger.info(
                        "🎚️ Cut-in RMS (raw=%.4f, aec=%.4f, baseline=%.4f, excess=%.4f) | VAD: %s | silero: %s (conf=%.2f) | threshold=%.4f",
                        rms_raw,
                        rms_cutin,
                        tts_rms_baseline,
                        max(0.0, rms_raw - tts_rms_baseline),
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                        config.vad_cut_in_rms,
                    )
                    logger.info(
                        "🎚️ Cut-in gate (ready=%s, silero_gate=%s, rms_excess=%.4f, rms_cutin=%.4f, hits=%d/%d, min_ms=%d)",
                        (tts_playback_start_ts is not None and int((now - tts_playback_start_ts) * 1000) >= config.vad_cut_in_min_ms),
                        silero_gate,
                        max(0.0, rms_raw - tts_rms_baseline),
                        rms_cutin,
                        cut_in_hits,
                        config.vad_cut_in_frames,
                        config.vad_cut_in_min_ms,
                    )
                    last_tts_meter_ts = now
                playback_ms = 0
                if tts_playback_start_ts is not None:
                    playback_ms = int((now - tts_playback_start_ts) * 1000)
                cut_in_ready = playback_ms >= config.vad_cut_in_min_ms
                rms_excess = max(0.0, rms_raw - tts_rms_baseline)
                alarm_ringing_during_tts = bool(alarm_manager and alarm_manager.ringing_alarms)
                if tts_playing and config.vad_cut_in_use_silero and silero_conf is not None:
                    if silero_conf <= 0.01 and vad_result_cutin.speech_detected and rms_excess >= config.vad_cut_in_rms:
                        silero_zero_hits += 1
                    else:
                        silero_zero_hits = 0
                    if silero_zero_hits >= 50:
                        logger.warning("Silero gate stuck at low confidence; disabling Silero cut-in gate")
                        config.vad_cut_in_use_silero = False
                        silero_gate = True
                cut_in_candidate = (not alarm_ringing_during_tts) and cut_in_ready and silero_gate and (
                    (vad_result_cutin.speech_detected and rms_excess >= config.vad_cut_in_rms)
                    or rms_cutin >= config.vad_cut_in_rms
                )
                if cut_in_candidate:
                    cut_in_hits += 1
                else:
                    cut_in_hits = 0
                if alarm_ringing_during_tts:
                    cut_in_hits = 0
                cut_in = cut_in_hits >= config.vad_cut_in_frames
                if cut_in and now - last_tts_speech_log_ts >= tts_speech_log_interval:
                    logger.info(
                        "✋ Cut-in triggered! (rms_raw=%.4f, rms_aec=%.4f, rms_excess=%.4f, vad=%s, silero=%s, silero_conf=%.2f, playback_ms=%d, cut_in_ready=%s)",
                        rms_raw,
                        rms_cutin,
                        rms_excess,
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                        playback_ms,
                        cut_in_ready,
                    )
                    print("✋ Cut-in triggered → stopping TTS", flush=True)
                    last_tts_speech_log_ts = now
                if cut_in:
                    dismissed_by_cut_in = await stop_ringing_alarms_immediately("voice cut-in")
                    if dismissed_by_cut_in > 0:
                        logger.info("✋ Voice cut-in dismissed active alarm ringing")
                    
                    # Track repeated cut-in events and adjust output volume
                    should_reduce_vol, vol_msg = cut_in_tracker.on_cut_in(now)
                    if should_reduce_vol and vol_msg:
                        logger.info(vol_msg)

                    if not chunk_frames:
                        cut_in_triggered_ts = now
                        chunk_start_ts = now
                        # Use minimal prebuffer for cut-in to keep interruptions tight
                        cut_in_pre_roll_frames = max(0, int(config.cut_in_pre_roll_ms / config.audio_frame_ms))
                        if cut_in_pre_roll_frames > 0:
                            prebuffer = ring_buffer.get_frames()
                            if len(prebuffer) > cut_in_pre_roll_frames:
                                prebuffer = prebuffer[-cut_in_pre_roll_frames:]
                            # Apply AEC to prebuffer frames to remove TTS while preserving early user speech
                            if aec and last_playback_frame:
                                aec_prebuffer = []
                                for pb_frame in prebuffer:
                                    try:
                                        aec_pb_frame = aec.process(pb_frame, last_playback_frame)
                                        aec_prebuffer.append(aec_pb_frame)
                                    except NotImplementedError:
                                        aec_prebuffer.append(pb_frame)
                                chunk_frames = aec_prebuffer
                            else:
                                chunk_frames = prebuffer
                            logger.info("📥 Cut-in prebuffer: %d frames (~%dms), AEC applied=%s", len(chunk_frames), len(chunk_frames) * config.audio_frame_ms, aec is not None and last_playback_frame is not None)
                        else:
                            chunk_frames = []
                            logger.info("📥 Cut-in prebuffer disabled (CUT_IN_PRE_ROLL_MS=0)")
                    chunk_frames.append(processed_frame)
                    last_speech_ts = now
                    last_activity_ts = now  # Reset wake timeout to keep listening after cut-in
                    wake_state = WakeState.AWAKE
                    last_wake_detected_ts = now
                    if tts_gain != 0.5:
                        tts_gain = 0.5
                    logger.info(
                        "⏹️ Setting tts_stop_event (rms_raw=%.4f, rms_aec=%.4f, rms_excess=%.4f, vad=%s, silero=%s, silero_conf=%.2f)",
                        rms_raw,
                        rms_cutin,
                        rms_excess,
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                    )
                    tts_stop_event.set()

                    if not cut_in_tts_hold_active:
                        cut_in_tts_hold_active = True
                        cut_in_tts_hold_started_ts = now
                        cut_in_tts_hold_request_id = current_tts_request_id if current_tts_request_id else current_request_id
                        dropped_tts = _tts_clear_all("cut-in")
                        logger.info(
                            "🛑 Cut-in hold activated (%dms) for req#%d. Dropped %d queued TTS item(s)",
                            max(0, config.vad_cut_in_tts_hold_timeout_ms),
                            cut_in_tts_hold_request_id,
                            dropped_tts,
                        )
                    
                    # Stop music playback when voice cut-in detected
                    if config.music_enabled and music_manager:
                        try:
                            is_playing = await music_manager.is_playing()
                            if is_playing:
                                logger.info("🎵 Pausing music for voice cut-in")
                                await music_manager.pause()
                                music_paused_for_wake = True
                                music_auto_resume_timer = 0.0
                        except Exception as e:
                            logger.debug("Error stopping music on cut-in: %s", e)

            if chunk_frames and chunk_start_ts is None:
                chunk_start_ts = now
                if last_speech_ts is None:
                    last_speech_ts = now
                logger.warning("Audio chunk had frames without start timestamp; recovering chunk timing")
            if tts_playing and last_speech_ts:
                silence_ms = int(((now - last_speech_ts) * 1000))
                if silence_ms >= config.vad_min_silence_ms and tts_gain != tts_base_gain:
                    tts_gain = tts_base_gain
                    logger.info("Mic speech ended → restoring TTS volume")

            if chunk_frames and chunk_start_ts is not None:
                chunk_duration_ms = int((now - chunk_start_ts) * 1000)
                silence_ms = int(((now - last_speech_ts) * 1000)) if last_speech_ts else 0

                if silence_ms >= config.vad_min_silence_ms or chunk_duration_ms >= config.chunk_max_ms:
                    pcm = b"".join(chunk_frames)
                    latency_ms = int((now - cut_in_triggered_ts) * 1000) if cut_in_triggered_ts else -1
                    print(f"📦 Audio chunk ready: {chunk_duration_ms}ms, {len(pcm)} bytes, silence={silence_ms}ms, latency={latency_ms}ms", flush=True)
                    logger.info(
                        "═══ AUDIO CHUNK: duration=%d ms, size=%d bytes, silence=%d ms, frames=%d, latency=%d ms (cut-in→send) ═══",
                        chunk_duration_ms,
                        len(pcm),
                        silence_ms,
                        len(chunk_frames),
                        latency_ms,
                    )
                    asyncio.create_task(
                        process_chunk(
                            pcm,
                            cut_in_triggered_ts,
                            chunk_start_ts,
                            recorder_capture_mode=bool(recorder_tool and recorder_tool.is_recording()),
                        )
                    )
                    ring_buffer.clear()
                    chunk_frames = []
                    chunk_start_ts = None
                    last_speech_ts = None
                    cut_in_triggered_ts = None

                    if recorder_hotword_mode:
                        wake_state = WakeState.ASLEEP
                        wake_sleep_ts = now
                        last_wake_detected_ts = None
                        if wake_detector and hasattr(wake_detector, 'reset_state'):
                            try:
                                wake_detector.reset_state()
                            except Exception:
                                pass

            if config.wake_word_enabled and (not continuous_mode) and wake_state == WakeState.AWAKE:
                if last_wake_detected_ts is None:
                    wake_state = WakeState.ASLEEP
                    wake_sleep_ts = now
                    recorder_stop_hotword_armed_ts = None
                    await asyncio.sleep(0)
                    continue
                inactive_ms = int((now - last_activity_ts) * 1000)
                timeout_ms = config.wake_word_timeout_ms
                # Log inactivity progress at 50% and again at 90% of timeout (rate-limited to once per second)
                if timeout_ms > 0 and inactive_ms > 0 and (now - last_timeout_progress_log_ts) >= 1.0:
                    pct = inactive_ms / timeout_ms
                    if pct >= 0.5:
                        logger.info(
                            "⏱ Listen timeout: %dms / %dms (%.0f%%) — still awake | state=%s tts=%s",
                            inactive_ms, timeout_ms, pct * 100,
                            state.value, tts_playing,
                        )
                        last_timeout_progress_log_ts = now
                if timeout_ms > 0 and inactive_ms >= timeout_ms:
                    # Don't timeout if TTS is playing, queued, or we're actively processing
                    debounce_pending = debounce_task is not None and not debounce_task.done()
                    has_pending_transcripts = bool(pending_transcripts)
                    if (
                        state in (VoiceState.IDLE, VoiceState.LISTENING)
                        and not tts_playing
                        and not _tts_has_pending()
                        and not debounce_pending
                        and not has_pending_transcripts
                        and active_transcriptions == 0
                    ):
                        wake_state = WakeState.ASLEEP
                        wake_sleep_ts = now
                        last_wake_detected_ts = None
                        recorder_stop_hotword_armed_ts = None
                        last_timeout_progress_log_ts = 0.0
                        if web_service:
                            if web_service._ui_control_state.get("mic_enabled", False):
                                web_service.update_ui_control_state(mic_enabled=False)
                            web_service.update_orchestrator_status(
                                wake_state=wake_state.value,
                                voice_state=state.value,
                                mic_enabled=False,
                            )
                        # Reset wake detector state to prevent immediate re-detection
                        if wake_detector and hasattr(wake_detector, 'reset_state'):
                            wake_detector.reset_state()
                        logger.info(
                            "😴 Wake timeout: %dms inactive (limit %dms) → asleep | mic_btn=red",
                            inactive_ms, timeout_ms,
                        )
                        if timeout_swoosh_sound:
                            try:
                                play_feedback_async(
                                    timeout_swoosh_sound,
                                    float(max(0.1, config.sleep_feedback_gain)),
                                    "sleep swoosh (timeout)",
                                )
                            except Exception as exc:
                                logger.debug("Failed to play timeout sleep cue: %s", exc)
                    else:
                        # Timeout elapsed but guards are blocking sleep — log reason once per second
                        if (now - last_timeout_progress_log_ts) >= 1.0:
                            blocking = []
                            if state not in (VoiceState.IDLE, VoiceState.LISTENING):
                                blocking.append(f"state={state.value}")
                            if tts_playing:
                                blocking.append("tts_playing")
                            if _tts_has_pending():
                                blocking.append("tts_pending")
                            if debounce_pending:
                                blocking.append("debounce_pending")
                            if has_pending_transcripts:
                                blocking.append("pending_transcripts")
                            if active_transcriptions > 0:
                                blocking.append(f"active_transcriptions={active_transcriptions}")
                            logger.info(
                                "⏱ Timeout elapsed (%dms) but sleep blocked by: %s",
                                inactive_ms, ", ".join(blocking) or "unknown",
                            )
                            last_timeout_progress_log_ts = now

            await asyncio.sleep(0)
    finally:
        if 'local_capture_paused_for_browser' in locals() and local_capture_paused_for_browser and hasattr(capture, "set_muted"):
            capture.set_muted(local_capture_prev_muted)
        if recordings_catalog:
            logger.info("Stopping recordings catalog...")
            await recordings_catalog.stop()
        if web_service:
            logger.info("Stopping embedded web UI service...")
            await web_service.stop()
        # Cleanup media key detector if running
        if media_key_detector:
            logger.info("Stopping media key detector...")
            await media_key_detector.stop()
        # Cleanup tool monitor if running
        if tool_monitor:
            logger.info("Stopping tool monitor...")
            await tool_monitor.stop()

        # Explicitly stop music playback on orchestrator shutdown so any active
        # native player process does not continue after this process exits.
        shutdown_music_manager = locals().get("music_manager")
        if shutdown_music_manager:
            try:
                logger.info("Stopping music playback for orchestrator shutdown...")
                await asyncio.wait_for(shutdown_music_manager.stop(), timeout=3.0)
            except Exception as exc:
                logger.warning("Failed to stop music playback during shutdown: %s", exc)

        # Close native music pools to release backend resources cleanly.
        shutdown_music_pool = locals().get("music_pool")
        if shutdown_music_pool:
            try:
                await asyncio.wait_for(shutdown_music_pool.close(), timeout=2.0)
            except Exception as exc:
                logger.debug("Failed to close music pool during shutdown: %s", exc)
        shutdown_music_control_pool = locals().get("music_control_pool")
        if shutdown_music_control_pool:
            try:
                await asyncio.wait_for(shutdown_music_control_pool.close(), timeout=2.0)
            except Exception as exc:
                logger.debug("Failed to close control music pool during shutdown: %s", exc)

        capture.stop()


def main() -> None:
    asyncio.run(run_orchestrator())


if __name__ == "__main__":
    main()
