from __future__ import annotations

import time
from typing import Any


_TOOLISH_ROLES = {"tool", "toolresult", "tool_result", "tool_use", "tooluse"}


def _flatten_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type") or "").strip().lower()
        if ctype in {"thinking", "reasoning", "toolcall", "toolresult"}:
            continue
        text_val = item.get("text")
        if isinstance(text_val, str) and text_val.strip():
            parts.append(text_val.strip())
    return "\n".join(parts).strip()


def _normalize_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            return ts / 1000.0
        return ts
    if isinstance(value, str):
        try:
            return _normalize_ts(float(value))
        except Exception:
            return time.time()
    return time.time()


def map_gateway_messages_to_voice_format(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map gateway chat.history items to the web UI chat shape.

    chat.history strips the JSONL envelope so each item is already the inner
    {role, content, timestamp} object.  We also handle the raw JSONL envelope
    shape {type:"message", message:{...}} in case the format ever changes.
    """
    mapped: list[dict[str, Any]] = []
    seq = 0

    for item in messages:
        if not isinstance(item, dict):
            continue

        # If still in JSONL envelope form: {type:"message", message:{role,...}}
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "message" and isinstance(item.get("message"), dict):
            message_obj = item["message"]
        elif item_type in {"", "compaction"} or item_type == "message":
            # Already unwrapped by readSessionMessages; treat item itself as message obj.
            # Skip non-message JSONL types (session, model_change, etc.) that have no role.
            message_obj = item
        else:
            # Unknown envelope type — skip
            continue
        role = str(message_obj.get("role") or "").strip().lower()
        if not role or role in _TOOLISH_ROLES:
            continue

        text = _flatten_text_content(message_obj.get("content"))
        if not text:
            text = str(message_obj.get("text") or "").strip()
        if not text:
            continue

        seq += 1
        mapped.append(
            {
                "id": seq,
                "role": role,
                "text": text,
                "ts": _normalize_ts(message_obj.get("timestamp") or item.get("timestamp")),
                "source": "gateway",
            }
        )

    return mapped
