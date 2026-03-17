import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse
from uuid import uuid4
import time

import requests
import websockets

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
except Exception:  # pragma: no cover - optional dependency check at runtime
    serialization = None
    Ed25519PrivateKey = None
    Ed25519PublicKey = None


logger = logging.getLogger("orchestrator.gateway.providers")


VOICE_OUTPUT_FORMAT_PROMPT = (
    "For longer answers, markdown formatting is allowed in your final visible reply when it improves clarity. "
    "Keep short spoken-style replies plain when formatting adds no value. "
    "If a diagram would help, include Mermaid markup wrapped in <mermaidchart>...</mermaidchart> tags. "
    "When returning Mermaid, always use those tags (do not return raw Mermaid outside tags). "
    "Inside those tags, include only Mermaid source and no extra prose or code fences."
)


@dataclass
class GatewayResponse:
    text: str
    run_id: str
    session_id: str


class BaseGateway:
    provider: str = "base"
    supports_listen: bool = False

    async def send_message(
        self,
        text: str,
        session_id: str,
        agent_id: str,
        metadata: Optional[dict] = None,
    ) -> Optional[str]:
        raise NotImplementedError

    async def listen(self) -> AsyncIterator[str]:
        if False:
            yield ""

    async def listen_steps(self) -> AsyncIterator[dict]:
        if False:
            yield {}
        return


class GenericGateway(BaseGateway):
    provider = "generic"
    supports_listen = True

    def __init__(
        self,
        http_url: str = "",
        http_endpoint: str = "/api/short",
        ws_url: str = "",
        timeout_s: int = 30,
        poll_endpoint: str = "/api/responses",
        poll_interval_s: float = 0.5,
    ) -> None:
        self.http_url = http_url.rstrip("/") if http_url else ""
        self.http_endpoint = http_endpoint if http_endpoint.startswith("/") else f"/{http_endpoint}"
        self.ws_url = ws_url
        self.timeout_s = timeout_s
        self.poll_endpoint = poll_endpoint if poll_endpoint.startswith("/") else f"/{poll_endpoint}"
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_messages: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_messages.clear()

        payload = {
            "text": text,
            "sessionId": session_id,
            "agentId": agent_id,
            "metadata": metadata or {},
        }

        if self.ws_url:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(json.dumps(payload))
                    message = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                    data = json.loads(message)
                    if isinstance(data, dict):
                        return data.get("text") or data.get("response") or data.get("message")
            except Exception as exc:
                logger.warning("Generic gateway WS failed (%s); attempting HTTP", exc)

        if not self.http_url:
            return None

        def _post() -> requests.Response:
            return requests.post(
                f"{self.http_url}{self.http_endpoint}",
                json=payload,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("text") or data.get("response") or data.get("message")
        return None

    async def listen(self) -> AsyncIterator[str]:
        """Poll for responses from HTTP gateway."""
        if not self.http_url:
            logger.warning("GenericGateway: http_url not configured; polling disabled")
            if False:
                yield ""
            return

        logger.info("GenericGateway polling: session=%s, interval=%.1fs", self._current_session_id, self.poll_interval_s)

        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)

                try:
                    def _get() -> requests.Response:
                        return requests.get(
                            f"{self.http_url}{self.poll_endpoint}",
                            params={"sessionId": self._current_session_id},
                            timeout=self.timeout_s,
                        )

                    response = await asyncio.to_thread(_get)
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()

                    data = response.json()
                    if not isinstance(data, (list, dict)):
                        continue

                    messages = data if isinstance(data, list) else [data]

                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue

                        text = msg.get("text") or msg.get("message") or msg.get("response")
                        if not isinstance(text, str) or not text.strip():
                            continue

                        msg_id = msg.get("id") or text
                        if msg_id not in self._yielded_messages:
                            self._yielded_messages.add(msg_id)
                            logger.info("← GenericGateway: %s", text[:80])
                            yield text

                except requests.RequestException as exc:
                    logger.debug("Poll request failed: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("GenericGateway polling error: %s", exc)


class OpenClawGateway(BaseGateway):
    provider = "openclaw"
    supports_listen = True

    def __init__(
        self,
        gateway_url: str,
        token: str,
        agent_id: str = "assistant",
        session_prefix: str = "voice",
        timeout_s: int = 30,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self.session_prefix = session_prefix
        self.timeout_s = timeout_s
        self._current_session_key: Optional[str] = None
        self._ws: Optional[Any] = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._incoming_texts: asyncio.Queue[str] = asyncio.Queue()
        self._step_events: asyncio.Queue[dict] = asyncio.Queue()
        self._connection_lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._hello_ok = False
        self._connect_nonce: Optional[str] = None
        self._connect_nonce_event = asyncio.Event()
        self._identity_path = Path.home() / ".openclaw" / "identity" / "device.json"
        self._device_identity_cache: Optional[dict[str, str]] = None
        self._last_streamed_text: str = ""
        self._last_reasoning_text: str = ""
        self._raw_dump_path = Path("/tmp/openclaw_gateway_stream.jsonl")

    def _dump_raw_frame(self, raw_message: str, *, parsed: Any = None, note: str = "") -> None:
        entry: dict[str, Any] = {
            "ts": time.time(),
            "session_key": self._current_session_key,
            "note": note,
            "raw": raw_message,
        }
        if parsed is not None:
            entry["parsed"] = parsed
        try:
            self._raw_dump_path.parent.mkdir(parents=True, exist_ok=True)
            with self._raw_dump_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("Failed to write OpenClaw raw frame dump: %s", exc)

    @staticmethod
    def _strip_heartbeat_markers(text: str) -> str:
        cleaned = re.sub(r"(?i)HEARTBEAT\s*[_ ]?OK(?:\s*NO\s*[_ ]?REPLY)?", " ", text)
        cleaned = re.sub(r"(?i)\bNO\s*[_ ]?REPLY\b", " ", cleaned)
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned

    @staticmethod
    def _split_reasoning_text(text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        lower = text.lower()
        start_tag = "<reasoning>"
        end_tag = "</reasoning>"
        visible_parts: list[str] = []
        reasoning_parts: list[str] = []
        cursor = 0

        while True:
            start = lower.find(start_tag, cursor)
            if start == -1:
                visible_parts.append(text[cursor:])
                break
            visible_parts.append(text[cursor:start])
            content_start = start + len(start_tag)
            end = lower.find(end_tag, content_start)
            if end == -1:
                reasoning_parts.append(text[content_start:])
                break
            reasoning_parts.append(text[content_start:end])
            cursor = end + len(end_tag)

        visible = "".join(visible_parts)
        reasoning = "\n".join(part.strip() for part in reasoning_parts if part and part.strip())
        return visible, reasoning

    @staticmethod
    def _extract_output_text(result_obj: Any) -> Optional[str]:
        """Extract text output from tool result object."""
        if not isinstance(result_obj, dict):
            if isinstance(result_obj, str) and result_obj.strip():
                return result_obj.strip()
            return None

        # Try common field names in order of preference
        for field in ["text", "output", "result", "stdout", "stderr", "message", "content", "body"]:
            value = result_obj.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return None

    async def _emit_step_event(self, name: str, phase: str, tool_call_id: str, details_obj: Any) -> None:
        try:
            details_text = details_obj if isinstance(details_obj, str) else json.dumps(details_obj, ensure_ascii=False)
        except Exception:
            details_text = str(details_obj)
        await self._step_events.put(
            {
                "name": str(name),
                "phase": str(phase or "update"),
                "toolCallId": str(tool_call_id or ""),
                "details": details_text,
            }
        )

    def _ws_is_open(self) -> bool:
        if self._ws is None:
            return False
        closed = getattr(self._ws, "closed", None)
        if closed is not None:
            return not bool(closed)
        open_attr = getattr(self._ws, "open", None)
        if open_attr is not None:
            return bool(open_attr)
        state = getattr(self._ws, "state", None)
        if state is not None:
            state_name = getattr(state, "name", str(state)).upper()
            if "OPEN" in state_name:
                return True
            if "CLOSED" in state_name or "CLOSING" in state_name:
                return False
        return True

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    def _load_or_create_device_identity(self) -> dict[str, str]:
        if self._device_identity_cache is not None:
            return self._device_identity_cache

        if serialization is None or Ed25519PrivateKey is None or Ed25519PublicKey is None:
            raise RuntimeError(
                "OpenClaw websocket device auth requires 'cryptography'. Add it to requirements and install dependencies."
            )

        def _from_existing(parsed: dict) -> Optional[dict[str, str]]:
            device_id = parsed.get("deviceId")
            public_pem = parsed.get("publicKeyPem")
            private_pem = parsed.get("privateKeyPem")
            if not all(isinstance(v, str) and v.strip() for v in (device_id, public_pem, private_pem)):
                return None

            public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
            if not isinstance(public_key, Ed25519PublicKey):
                return None
            public_raw = public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            derived_id = hashlib.sha256(public_raw).hexdigest()
            return {
                "deviceId": derived_id,
                "publicKeyPem": public_pem,
                "privateKeyPem": private_pem,
                "publicKey": self._b64url_encode(public_raw),
            }

        try:
            if self._identity_path.exists():
                parsed = json.loads(self._identity_path.read_text("utf-8"))
                identity = _from_existing(parsed)
                if identity is not None:
                    self._device_identity_cache = identity
                    return identity
        except Exception as exc:
            logger.debug("Could not load device identity from %s: %s", self._identity_path, exc)

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        device_id = hashlib.sha256(public_raw).hexdigest()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        identity = {
            "deviceId": device_id,
            "publicKeyPem": public_pem,
            "privateKeyPem": private_pem,
            "publicKey": self._b64url_encode(public_raw),
        }
        self._device_identity_cache = identity

        try:
            self._identity_path.parent.mkdir(parents=True, exist_ok=True)
            stored = {
                "version": 1,
                "deviceId": device_id,
                "publicKeyPem": public_pem,
                "privateKeyPem": private_pem,
                "createdAtMs": int(time.time() * 1000),
            }
            self._identity_path.write_text(f"{json.dumps(stored, indent=2)}\n", "utf-8")
            try:
                self._identity_path.chmod(0o600)
            except Exception:
                pass
            logger.debug("Device identity persisted to %s", self._identity_path)
        except Exception as exc:
            logger.warning(
                "Could not persist device identity to %s (will use in-memory only): %s",
                self._identity_path,
                exc,
            )

        return identity

    def _build_device_auth_payload(
        self,
        *,
        device_id: str,
        client_id: str,
        client_mode: str,
        role: str,
        scopes: list[str],
        signed_at_ms: int,
        token: str,
        nonce: Optional[str],
    ) -> str:
        version = "v2" if nonce else "v1"
        parts = [
            version,
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token,
        ]
        if version == "v2":
            parts.append(nonce or "")
        return "|".join(parts)

    def _sign_device_payload(self, private_key_pem: str, payload: str) -> str:
        if serialization is None or Ed25519PrivateKey is None:
            raise RuntimeError("cryptography is required for OpenClaw device auth")
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise RuntimeError("invalid Ed25519 private key for OpenClaw device auth")
        signature = private_key.sign(payload.encode("utf-8"))
        return self._b64url_encode(signature)

    async def _send_request(self, method: str, params: dict, timeout_s: Optional[float] = None) -> dict:
        if not self._ws_is_open() or self._ws is None:
            raise RuntimeError("OpenClaw WebSocket is not connected")

        request_id = str(uuid4())
        frame = {
            "type": "req",
            "id": request_id,
            "method": method,
            "params": params,
        }
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = fut
        await self._ws.send(json.dumps(frame))
        try:
            response = await asyncio.wait_for(fut, timeout=timeout_s or self.timeout_s)
            if not isinstance(response, dict):
                raise RuntimeError("Invalid response frame from OpenClaw")
            return response
        except Exception:
            stale = self._pending_requests.pop(request_id, None)
            if stale is not None and not stale.done():
                stale.cancel()
            raise

    async def _ensure_connected(self) -> None:
        async with self._connection_lock:
            if self._ws_is_open():
                return

            ws_url = self.gateway_url.replace("http://", "ws://").replace("https://", "wss://")

            parsed_url = urlparse(ws_url)
            host = parsed_url.hostname or "localhost"
            port = parsed_url.port or (443 if parsed_url.scheme == "wss" else 80)

            additional_headers = {
                "Authorization": f"Bearer {self.token}",
                "Host": f"{host}:{port}" if port not in (80, 443) else host,
            }

            try:
                self._ws = await websockets.connect(ws_url, additional_headers=additional_headers)
                logger.info("OpenClaw WebSocket connected to %s", ws_url)

                self._connect_nonce = None
                self._connect_nonce_event = asyncio.Event()

                if self._reader_task is None or self._reader_task.done():
                    self._reader_task = asyncio.create_task(self._read_frames())

                nonce: Optional[str] = None
                try:
                    await asyncio.wait_for(self._connect_nonce_event.wait(), timeout=0.75)
                    nonce = self._connect_nonce
                except asyncio.TimeoutError:
                    nonce = None

                identity = self._load_or_create_device_identity()
                client_id = "gateway-client"
                client_mode = "backend"
                role = "operator"
                scopes = ["operator.read", "operator.write", "operator.admin"]
                signed_at_ms = int(time.time() * 1000)
                device_payload = self._build_device_auth_payload(
                    device_id=identity["deviceId"],
                    client_id=client_id,
                    client_mode=client_mode,
                    role=role,
                    scopes=scopes,
                    signed_at_ms=signed_at_ms,
                    token=self.token,
                    nonce=nonce,
                )
                signature = self._sign_device_payload(identity["privateKeyPem"], device_payload)

                logger.debug(
                    "Device auth: id=%s, nonce=%s, scopes=%s",
                    identity["deviceId"][:16],
                    nonce[:16] if nonce else "none",
                    ",".join(scopes),
                )

                connect_params = {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": client_id,
                        "displayName": "OpenClaw Voice Orchestrator",
                        "version": "1.0.0",
                        "platform": "python",
                        "mode": client_mode,
                    },
                    "caps": ["tool-events"],
                    "role": role,
                    "scopes": scopes,
                    "auth": {"token": self.token},
                    "device": {
                        "id": identity["deviceId"],
                        "publicKey": identity["publicKey"],
                        "signature": signature,
                        "signedAt": signed_at_ms,
                        "nonce": nonce,
                    },
                }
                res = await self._send_request("connect", connect_params, timeout_s=self.timeout_s)
                if not bool(res.get("ok")):
                    err = (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict) else "connect failed"
                    raise RuntimeError(f"OpenClaw connect failed: {err}")
                self._hello_ok = True
            except Exception as exc:
                logger.error("Failed to connect to OpenClaw WebSocket: %s", exc)
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                self._ws = None
                raise

    async def _read_frames(self) -> None:
        try:
            while self._ws and self._ws_is_open():
                try:
                    message = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout_s)
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        self._dump_raw_frame(message, note="json_decode_error")
                        logger.debug("Failed to parse WebSocket message: %s", message[:100])
                        continue

                    self._dump_raw_frame(message, parsed=data)

                    if not isinstance(data, dict):
                        continue

                    frame_type = data.get("type")
                    if frame_type == "res":
                        resp_id = data.get("id")
                        if isinstance(resp_id, str) and resp_id in self._pending_requests:
                            fut = self._pending_requests.pop(resp_id)
                            if not fut.done():
                                fut.set_result(data)
                        continue

                    if frame_type == "event":
                        event_name = data.get("event")
                        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

                        if event_name == "connect.challenge":
                            nonce = payload.get("nonce")
                            if isinstance(nonce, str) and nonce.strip():
                                self._connect_nonce = nonce.strip()
                                self._connect_nonce_event.set()
                            continue

                        if event_name == "agent":
                            agent_stream = payload.get("stream")
                            if agent_stream == "tool":
                                step_data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                                if not isinstance(step_data, dict):
                                    step_data = {}
                                name = (
                                    step_data.get("name")
                                    or step_data.get("tool")
                                    or step_data.get("toolName")
                                    or step_data.get("tool_name")
                                    or ""
                                )
                                raw_phase = (
                                    step_data.get("phase")
                                    or step_data.get("status")
                                    or step_data.get("state")
                                    or step_data.get("event")
                                    or ""
                                )
                                phase_text = str(raw_phase).strip().lower()
                                if phase_text in {"start", "begin", "started", "call_start", "running"}:
                                    phase = "start"
                                elif phase_text in {"end", "ended", "finish", "finished", "done", "complete", "completed", "success", "final"}:
                                    phase = "end"
                                else:
                                    phase = phase_text or "update"

                                tool_call_id = (
                                    step_data.get("toolCallId")
                                    or step_data.get("tool_call_id")
                                    or step_data.get("callId")
                                    or ""
                                )
                                if name:
                                    details_obj = {
                                        k: v
                                        for k, v in step_data.items()
                                        if k not in {"name", "tool", "toolName", "tool_name", "phase", "status", "state", "event"}
                                    }
                                    # For read/write operations at completion, explicitly extract and include output text
                                    if phase == "end" and any(x in str(name).lower() for x in ["read", "write", "create", "insert", "replace"]):
                                        result_val = step_data.get("result")
                                        if result_val is not None:
                                            extracted_text = self._extract_output_text(result_val)
                                            if extracted_text and "outputText" not in details_obj:
                                                details_obj["outputText"] = extracted_text
                                    await self._emit_step_event(str(name), str(phase), str(tool_call_id), details_obj)
                                    logger.debug(
                                        "← OpenClaw tool[%s] phase=%s id=%s",
                                        name,
                                        phase,
                                        str(tool_call_id)[:8] if tool_call_id else "",
                                    )
                                continue

                            if agent_stream in {"lifecycle", "compaction", "reasoning"}:
                                step_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                                if not isinstance(step_data, dict):
                                    step_data = {}
                                phase = str(step_data.get("phase") or "update").strip().lower() or "update"
                                await self._emit_step_event(str(agent_stream), phase, "", step_data)
                                continue

                            if agent_stream == "assistant":
                                step_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                                if isinstance(step_data, dict):
                                    reasoning_text = (
                                        step_data.get("reasoning")
                                        or step_data.get("thinking")
                                        or step_data.get("analysis")
                                        or step_data.get("thought")
                                        or ""
                                    )
                                    if isinstance(reasoning_text, str) and reasoning_text.strip():
                                        await self._emit_step_event("reasoning", "interim", "", reasoning_text.strip())

                            data_obj = payload.get("data")
                            message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else None
                            content_items = []
                            if isinstance(data_obj, dict) and isinstance(data_obj.get("content"), list):
                                content_items.extend([c for c in data_obj.get("content", []) if isinstance(c, dict)])
                            if message_obj and isinstance(message_obj.get("content"), list):
                                content_items.extend([c for c in message_obj.get("content", []) if isinstance(c, dict)])

                            role = ""
                            if isinstance(data_obj, dict):
                                role = str(data_obj.get("role") or "")
                            if not role and message_obj:
                                role = str(message_obj.get("role") or "")

                            for item in content_items:
                                item_type = str(item.get("type") or "").strip()
                                if item_type == "toolCall":
                                    await self._emit_step_event(
                                        str(item.get("name") or item.get("toolName") or "tool"),
                                        "start",
                                        str(item.get("id") or item.get("toolCallId") or ""),
                                        item,
                                    )
                                elif role == "toolResult" or item_type == "toolResult":
                                    await self._emit_step_event(
                                        str((data_obj or {}).get("toolName") or (message_obj or {}).get("toolName") or item.get("toolName") or "tool"),
                                        "end",
                                        str((data_obj or {}).get("toolCallId") or (message_obj or {}).get("toolCallId") or item.get("toolCallId") or item.get("id") or ""),
                                        {
                                            "message": data_obj if isinstance(data_obj, dict) else message_obj,
                                            "content": item,
                                        },
                                    )

                        text: Optional[str] = None
                        if isinstance(payload.get("text"), str):
                            text = payload.get("text")
                        elif isinstance(payload.get("delta"), str):
                            text = payload.get("delta")
                        elif isinstance(payload.get("message"), str):
                            text = payload.get("message")
                        elif isinstance(payload.get("data"), dict):
                            inner = payload.get("data")
                            if isinstance(inner.get("text"), str):
                                text = inner.get("text")
                            elif isinstance(inner.get("delta"), str):
                                text = inner.get("delta")

                        if isinstance(text, str) and text.strip():
                            full_text = text.strip()
                            visible_text, reasoning_text = self._split_reasoning_text(full_text)

                            if reasoning_text:
                                if reasoning_text.startswith(self._last_reasoning_text):
                                    reasoning_delta = reasoning_text[len(self._last_reasoning_text):].strip()
                                else:
                                    reasoning_delta = reasoning_text.strip()
                                if reasoning_delta:
                                    await self._emit_step_event("reasoning", "interim", "", reasoning_delta)
                                self._last_reasoning_text = reasoning_text
                            elif self._last_reasoning_text:
                                self._last_reasoning_text = ""

                            if full_text.startswith(self._last_streamed_text):
                                visible_prev, _ = self._split_reasoning_text(self._last_streamed_text)
                                if visible_text.startswith(visible_prev):
                                    delta = visible_text[len(visible_prev):].rstrip()
                                else:
                                    delta = visible_text.rstrip()
                                if delta:
                                    cleaned = delta
                                    cleaned = cleaned.replace("**", "").replace("*", "")
                                    cleaned = self._strip_heartbeat_markers(cleaned)
                                    if cleaned.lstrip().startswith("NO_RE"):
                                        logger.debug("Filtered NO_REPLY marker from delta")
                                        self._last_streamed_text = full_text
                                        continue

                                    if cleaned.strip():
                                        await self._incoming_texts.put(cleaned.rstrip())
                                        logger.info("← OpenClaw event[%s] delta: %s", event_name, cleaned.rstrip()[:80])
                                self._last_streamed_text = full_text
                            else:
                                cleaned = visible_text.replace("**", "").replace("*", "")
                                cleaned = self._strip_heartbeat_markers(cleaned)
                                if not cleaned.startswith("NO_RE"):
                                    if cleaned.strip():
                                        await self._incoming_texts.put(cleaned)
                                        logger.info("← OpenClaw event[%s]: %s", event_name, cleaned[:80])
                                else:
                                    logger.debug("Filtered NO_REPLY marker from full text")
                                self._last_streamed_text = full_text

                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as exc:
                    logger.debug("Error in OpenClaw frame reader: %s", exc)
                    break
        except Exception as exc:
            logger.warning("OpenClaw frame reader error: %s", exc)
        finally:
            for req_id, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_exception(RuntimeError("OpenClaw websocket disconnected"))
                self._pending_requests.pop(req_id, None)
            logger.info("OpenClaw frame reader stopped")

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        resolved_agent_id = agent_id or self.agent_id
        session_key = f"agent:{resolved_agent_id}:{session_id}"
        self._current_session_key = session_key

        self._last_streamed_text = ""
        self._last_reasoning_text = ""

        await self._ensure_connected()

        if not self._ws_is_open():
            raise RuntimeError("OpenClaw WebSocket connection failed")

        payload = {
            "message": text,
            "sessionKey": session_key,
            "agentId": resolved_agent_id,
            "channel": "last",
            "deliver": True,
            "extraSystemPrompt": VOICE_OUTPUT_FORMAT_PROMPT,
            "idempotencyKey": str(uuid4()),
        }

        res = await self._send_request("agent", payload, timeout_s=self.timeout_s)
        if not bool(res.get("ok")):
            err = (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict) else "agent request failed"
            raise RuntimeError(f"OpenClaw agent request failed: {err}")

        payload_obj = res.get("payload") if isinstance(res.get("payload"), dict) else {}
        immediate = payload_obj.get("text") or payload_obj.get("message")
        return immediate if isinstance(immediate, str) else None

    async def inject_message(
        self,
        session_key: str,
        message: str,
        label: Optional[str] = None,
    ) -> None:
        await self._ensure_connected()
        params: dict = {"sessionKey": session_key, "message": message}
        if label is not None:
            params["label"] = label
        res = await self._send_request("chat.inject", params, timeout_s=self.timeout_s)
        if not bool(res.get("ok")):
            err = (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict) else "chat.inject failed"
            raise RuntimeError(f"OpenClaw chat.inject failed: {err}")

    async def listen(self) -> AsyncIterator[str]:
        await self._ensure_connected()

        try:
            while self._ws and self._ws_is_open():
                text = await self._incoming_texts.get()
                if text:
                    yield text
        except Exception as exc:
            logger.warning("OpenClaw listen error: %s", exc)

    async def listen_steps(self) -> AsyncIterator[dict]:
        await self._ensure_connected()
        try:
            while self._ws and self._ws_is_open():
                step = await self._step_events.get()
                yield step
        except Exception as exc:
            logger.warning("OpenClaw listen_steps error: %s", exc)
