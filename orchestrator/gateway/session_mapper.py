from __future__ import annotations

import time
from typing import Any


_TOOLISH_ROLES = {"tool", "toolresult", "tool_result", "tool_use", "tooluse"}
_TOOL_CALL_TYPES = {"toolcall", "tool_call", "tool_use", "tooluse"}
_TOOL_RESULT_TYPES = {"toolresult", "tool_result", "tool_result_error"}
_THINKING_TYPES = {"thinking", "reasoning"}


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


def _normalize_block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or "").strip().lower()


def _next_id(seq: int) -> tuple[int, int]:
    seq += 1
    return seq, seq


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _json_details(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _tool_summary_text(block: dict[str, Any]) -> str:
    for key in ("result", "partialResult", "output", "stdout", "stderr", "message", "text", "content"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    text_val = item.get("text")
                    if isinstance(text_val, str) and text_val.strip():
                        parts.append(text_val.strip())
            if parts:
                return "\n".join(parts).strip()
    return ""


def _map_content_block_to_voice_messages(
    *,
    block: dict[str, Any],
    message_obj: dict[str, Any],
    role: str,
    ts: float,
    source: str,
    mapped: list[dict[str, Any]],
    seq: int,
) -> int:
    block_type = _normalize_block_type(block)
    if block_type in _THINKING_TYPES:
        thinking_text = _stringify(block.get("thinking") or block.get("text"))
        if not thinking_text:
            return seq
        seq, msg_id = _next_id(seq)
        mapped.append(
            {
                "id": msg_id,
                "role": "interim",
                "text": "reasoning",
                "phase": "update",
                "details": _json_details({"text": thinking_text, "type": block_type}),
                "ts": ts,
                "source": source,
            }
        )
        return seq

    if block_type in _TOOL_CALL_TYPES:
        tool_name = _stringify(block.get("name") or role or "tool") or "tool"
        tool_call_id = _stringify(
            block.get("id")
            or block.get("toolCallId")
            or block.get("tool_call_id")
            or message_obj.get("toolCallId")
            or message_obj.get("tool_call_id")
        )
        phase = _stringify(block.get("phase") or "start") or "start"
        seq, msg_id = _next_id(seq)
        mapped.append(
            {
                "id": msg_id,
                "role": "step",
                "text": tool_name,
                "name": tool_name,
                "phase": phase,
                "tool_call_id": tool_call_id,
                "details": _json_details(block),
                "ts": ts,
                "source": source,
            }
        )
        return seq

    if block_type in _TOOL_RESULT_TYPES:
        tool_name = _stringify(block.get("name") or message_obj.get("toolName") or message_obj.get("tool_name") or role or "tool") or "tool"
        tool_call_id = _stringify(
            block.get("toolCallId")
            or block.get("tool_call_id")
            or block.get("id")
            or message_obj.get("toolCallId")
            or message_obj.get("tool_call_id")
        )
        phase = "error" if block_type.endswith("_error") or block.get("is_error") is True else "result"
        payload = dict(block)
        summary = _tool_summary_text(block)
        if summary and "result" not in payload and "text" not in payload:
            payload["result"] = summary
        seq, msg_id = _next_id(seq)
        mapped.append(
            {
                "id": msg_id,
                "role": "step",
                "text": tool_name,
                "name": tool_name,
                "phase": phase,
                "tool_call_id": tool_call_id,
                "details": _json_details(payload),
                "ts": ts,
                "source": source,
            }
        )
        return seq

    return seq


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
        if not role:
            continue

        ts = _normalize_ts(message_obj.get("timestamp") or item.get("timestamp"))
        source = "gateway"

        content = message_obj.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                seq = _map_content_block_to_voice_messages(
                    block=block,
                    message_obj=message_obj,
                    role=role,
                    ts=ts,
                    source=source,
                    mapped=mapped,
                    seq=seq,
                )

        if role in _TOOLISH_ROLES:
            continue

        text = _flatten_text_content(message_obj.get("content"))
        if not text:
            text = str(message_obj.get("text") or "").strip()
        if not text:
            continue

        seq, msg_id = _next_id(seq)
        mapped.append(
            {
                "id": msg_id,
                "role": role,
                "text": text,
                "ts": ts,
                "source": source,
            }
        )

    return mapped
