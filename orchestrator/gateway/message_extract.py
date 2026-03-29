import json
import re


def extract_text_from_gateway_message(message: str) -> str:
    """Extract text from gateway message. Handles JSON payloads or plain text."""
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return message.rstrip()

    if isinstance(payload, (str, int, float, bool)):
        if isinstance(payload, str):
            return payload.rstrip()
        return str(payload).strip()

    if isinstance(payload, dict):
        if "text" in payload:
            value = payload["text"]
            if isinstance(value, str):
                return value.rstrip()
            return str(value).strip()
        if "content" in payload:
            content = payload["content"]
            if isinstance(content, str):
                return content.rstrip()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        part = block.get("text", "")
                        if isinstance(part, str):
                            parts.append(part.rstrip())
                        else:
                            parts.append(str(part).strip())
                return "\n".join([p for p in parts if p])
        if "data" in payload and isinstance(payload["data"], dict):
            text = payload["data"].get("text")
            if text:
                if isinstance(text, str):
                    return text.rstrip()
                return str(text).strip()
    return ""


def strip_gateway_control_markers(text: str) -> str:
    """Remove non-user-facing control markers that can leak from upstream agent automation."""
    if not text:
        return ""
    cleaned = re.sub(r"(?i)HEARTBEAT\s*[_ ]?OK(?:\s*NO\s*[_ ]?REPLY)?", " ", text)
    cleaned = re.sub(r"(?i)\bNO\s*[_ ]?REPLY\b", " ", cleaned)
    # Preserve markdown structure by only normalizing horizontal whitespace.
    # Using \s here would collapse newlines and break headings/lists/links formatting.
    cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned
