import asyncio
import base64
import hashlib
import json
import logging
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
                    
                    # Handle both list and dict responses
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
        self._connection_lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._hello_ok = False
        self._connect_nonce: Optional[str] = None
        self._connect_nonce_event = asyncio.Event()
        self._identity_path = Path.home() / ".openclaw" / "identity" / "device.json"
        self._device_identity_cache: Optional[dict[str, str]] = None
        self._last_streamed_text: str = ""  # Track last text for delta extraction

    def _ws_is_open(self) -> bool:
        """Version-tolerant check for websocket connection state."""
        if self._ws is None:
            return False

        # websockets<=11: protocol.closed / protocol.open
        closed = getattr(self._ws, "closed", None)
        if closed is not None:
            return not bool(closed)

        open_attr = getattr(self._ws, "open", None)
        if open_attr is not None:
            return bool(open_attr)

        # websockets>=12+: connection.state enum/string
        state = getattr(self._ws, "state", None)
        if state is not None:
            state_name = getattr(state, "name", str(state)).upper()
            if "OPEN" in state_name:
                return True
            if "CLOSED" in state_name or "CLOSING" in state_name:
                return False

        # If state is unknown, assume open and let send/recv surface errors.
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

        # Try to load existing identity from disk
        try:
            if self._identity_path.exists():
                parsed = json.loads(self._identity_path.read_text("utf-8"))
                identity = _from_existing(parsed)
                if identity is not None:
                    self._device_identity_cache = identity
                    return identity
        except Exception as exc:
            logger.debug("Could not load device identity from %s: %s", self._identity_path, exc)

        # Generate new identity
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

        # Try to persist identity to disk (non-blocking on failure)
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
        """Ensure WebSocket connection is established and protocol handshake is complete."""
        async with self._connection_lock:
            if self._ws_is_open():
                return
            
            ws_url = self.gateway_url.replace("http://", "ws://").replace("https://", "wss://")
            
            # Extract host for local detection (OpenClaw checks Host header and client IP for local auto-pairing)
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

                # Start frame reader
                if self._reader_task is None or self._reader_task.done():
                    self._reader_task = asyncio.create_task(self._read_frames())

                # Some deployments send a connect challenge nonce prior to connect.
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

                # Handshake: first frame must be connect request.
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
                    "caps": [],
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
        """Continuously read frames and dispatch to pending request futures / text queue."""
        try:
            while self._ws and self._ws_is_open():
                try:
                    message = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout_s)
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse WebSocket message: %s", message[:100])
                        continue
                    
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

                        # Best-effort text extraction for streaming agent responses.
                        text: Optional[str] = None
                        if isinstance(payload.get("text"), str):
                            text = payload.get("text")
                        elif isinstance(payload.get("message"), str):
                            text = payload.get("message")
                        elif isinstance(payload.get("data"), dict):
                            inner = payload.get("data")
                            if isinstance(inner.get("text"), str):
                                text = inner.get("text")

                        if isinstance(text, str) and text.strip():
                            # Extract delta: only new text not seen in previous stream updates
                            full_text = text.strip()
                            if full_text.startswith(self._last_streamed_text):
                                # Only strip trailing whitespace to preserve leading spaces (word boundaries)
                                delta = full_text[len(self._last_streamed_text):].rstrip()
                                if delta:
                                    # Clean delta for TTS: remove markdown formatting and filter special markers
                                    cleaned = delta
                                    # Remove markdown bold/italic
                                    cleaned = cleaned.replace("**", "").replace("*", "")
                                    # Filter out NO_REPLY markers
                                    if cleaned.lstrip().startswith("NO_RE"):
                                        logger.debug("Filtered NO_REPLY marker from delta")
                                        self._last_streamed_text = full_text
                                        continue
                                    
                                    if cleaned.strip():
                                        # Preserve leading space but strip trailing
                                        await self._incoming_texts.put(cleaned.rstrip())
                                        logger.info("← OpenClaw event[%s] delta: %s", event_name, cleaned.rstrip()[:80])
                                self._last_streamed_text = full_text
                            else:
                                # New message or reset - queue full text with cleaning
                                cleaned = full_text.replace("**", "").replace("*", "")
                                if not cleaned.startswith("NO_RE"):
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
            # Fail any pending requests when the socket reader stops.
            for req_id, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_exception(RuntimeError("OpenClaw websocket disconnected"))
                self._pending_requests.pop(req_id, None)
            logger.info("OpenClaw frame reader stopped")

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        # For OpenClaw: sessionKey must be in canonical format: "agent:{agentId}:{session_id}"
        # This ensures the session is explicitly associated with the correct agent
        resolved_agent_id = agent_id or self.agent_id
        session_key = f"agent:{resolved_agent_id}:{session_id}"
        self._current_session_key = session_key
        
        # Reset streaming state for new message
        self._last_streamed_text = ""
        
        await self._ensure_connected()
        
        if not self._ws_is_open():
            raise RuntimeError("OpenClaw WebSocket connection failed")

        # OpenClaw agent method schema only accepts specific fields with no additionalProperties
        payload = {
            "message": text,
            "sessionKey": session_key,
            "agentId": resolved_agent_id,
            "channel": "last",
            "deliver": True,
            "idempotencyKey": str(uuid4()),
        }

        # Use RPC method expected by OpenClaw gateway; returns accepted/run metadata.
        res = await self._send_request("agent", payload, timeout_s=self.timeout_s)
        if not bool(res.get("ok")):
            err = (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict) else "agent request failed"
            raise RuntimeError(f"OpenClaw agent request failed: {err}")

        # Immediate text may exist on payload for short responses, but streaming usually arrives as events.
        payload_obj = res.get("payload") if isinstance(res.get("payload"), dict) else {}
        immediate = payload_obj.get("text") or payload_obj.get("message")
        return immediate if isinstance(immediate, str) else None

    async def listen(self) -> AsyncIterator[str]:
        """Listen for agent responses via persistent WebSocket connection."""
        await self._ensure_connected()
        
        try:
            while self._ws and self._ws_is_open():
                text = await self._incoming_texts.get()
                if text:
                    yield text
        except Exception as exc:
            logger.warning("OpenClaw listen error: %s", exc)


class ZeroClawGateway(BaseGateway):
    provider = "zeroclaw"
    supports_listen = True

    def __init__(
        self,
        gateway_url: str,
        webhook_token: str,
        channel: str = "voice",
        timeout_s: int = 30,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.webhook_token = webhook_token
        self.channel = channel
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_messages: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_messages.clear()
        
        headers = {
            "Authorization": f"Bearer {self.webhook_token}",
            "Content-Type": "application/json",
            "X-Session-Id": session_id,
            "X-Agent-Id": agent_id or "default",
            "X-Channel": self.channel,
        }
        payload = {
            "text": text,
            "metadata": {
                "channel": self.channel,
                **(metadata or {}),
            },
        }

        def _post() -> requests.Response:
            return requests.post(
                f"{self.gateway_url}/webhook",
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("response") or data.get("text") or data.get("message")
        return None

    async def listen(self) -> AsyncIterator[str]:
        """Poll for responses from ZeroClaw gateway."""
        logger.info("ZeroClawGateway polling: session=%s, interval=%.1fs", self._current_session_id, self.poll_interval_s)
        
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                
                try:
                    def _get() -> requests.Response:
                        return requests.get(
                            f"{self.gateway_url}/responses",
                            headers={
                                "Authorization": f"Bearer {self.webhook_token}",
                                "X-Session-Id": self._current_session_id,
                            },
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
                        
                        text = msg.get("response") or msg.get("text") or msg.get("message")
                        if not isinstance(text, str) or not text.strip():
                            continue
                        
                        msg_id = msg.get("id") or text
                        if msg_id not in self._yielded_messages:
                            self._yielded_messages.add(msg_id)
                            logger.info("← ZeroClaw: %s", text[:80])
                            yield text
                
                except requests.RequestException as exc:
                    logger.debug("ZeroClaw poll request failed: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("ZeroClawGateway polling error: %s", exc)


class TinyClawGateway(BaseGateway):
    provider = "tinyclaw"
    supports_listen = True

    def __init__(
        self,
        tinyclaw_home: str,
        agent_id: str = "default",
        timeout_s: int = 30,
    ) -> None:
        self.tinyclaw_home = Path(tinyclaw_home)
        self.agent_id = agent_id
        self.timeout_s = timeout_s
        self.queue_dir = self.tinyclaw_home / "queue"
        self._pending_message_ids: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        message_id = f"{int(time.time() * 1000)}-{uuid4()}"
        self._pending_message_ids.add(message_id)
        
        await asyncio.to_thread(self._ensure_queue_dirs)

        payload = {
            "id": message_id,
            "sessionId": session_id,
            "agentId": agent_id or self.agent_id,
            "message": text,
            "timestamp": int(time.time() * 1000),
            "source": "voice",
            "metadata": metadata or {},
        }

        incoming_path = self.queue_dir / "incoming" / f"{message_id}.json"
        await asyncio.to_thread(incoming_path.write_text, json.dumps(payload, indent=2), "utf-8")

        response = await self._wait_for_response(message_id)
        if isinstance(response, dict):
            return response.get("response") or response.get("message")
        return None

    def _ensure_queue_dirs(self) -> None:
        for name in ("incoming", "outgoing", "processing"):
            (self.queue_dir / name).mkdir(parents=True, exist_ok=True)

    async def _wait_for_response(self, message_id: str) -> dict:
        outgoing_path = self.queue_dir / "outgoing" / f"{message_id}.json"
        start = time.monotonic()
        while time.monotonic() - start < self.timeout_s:
            if outgoing_path.exists():
                data = json.loads(outgoing_path.read_text("utf-8"))
                try:
                    outgoing_path.unlink()
                except OSError:
                    pass
                self._pending_message_ids.discard(message_id)
                return data
            await asyncio.sleep(0.1)
        self._pending_message_ids.discard(message_id)
        raise TimeoutError(f"TinyClaw response timeout for {message_id}")

    async def listen(self) -> AsyncIterator[str]:
        """Listen for responses from TinyClaw queue."""
        logger.info("TinyClawGateway listening for responses in %s", self.queue_dir)
        await asyncio.to_thread(self._ensure_queue_dirs)
        
        try:
            while True:
                await asyncio.sleep(0.1)
                
                try:
                    outgoing_dir = self.queue_dir / "outgoing"
                    if not outgoing_dir.exists():
                        continue
                    
                    response_files = list(outgoing_dir.glob("*.json"))
                    for response_file in response_files:
                        # Only process responses for messages we didn't already handle
                        msg_id = response_file.stem
                        if msg_id in self._pending_message_ids:
                            continue
                        
                        try:
                            data = json.loads(response_file.read_text("utf-8"))
                            text = data.get("response") or data.get("message")
                            if isinstance(text, str) and text.strip():
                                logger.info("← TinyClaw: %s", text[:80])
                                # Try to delete after reading
                                try:
                                    response_file.unlink()
                                except OSError:
                                    pass
                                yield text
                        except (json.JSONDecodeError, OSError):
                            continue
                except Exception as exc:
                    logger.debug("TinyClawGateway listen error: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("TinyClawGateway listener error: %s", exc)


class IronClawGateway(BaseGateway):
    provider = "ironclaw"
    supports_listen = True

    def __init__(
        self,
        gateway_url: str,
        token: str,
        agent_id: str = "default",
        use_websocket: bool = True,
        timeout_s: int = 30,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self.use_websocket = use_websocket
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_messages: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_messages.clear()
        
        if self.use_websocket:
            return await self._send_ws(text, session_id, agent_id, metadata)
        return await self._send_http(text, session_id, agent_id, metadata)

    async def _send_ws(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict]) -> Optional[str]:
        ws_url = f"{self.gateway_url.replace('http', 'ws').rstrip('/')}/ws"
        run_id = str(uuid4())
        payload = {
            "type": "message",
            "runId": run_id,
            "sessionId": session_id,
            "agentId": agent_id or self.agent_id,
            "text": text,
            "metadata": metadata or {},
        }
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps(payload))
            while True:
                message = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                data = json.loads(message)
                if data.get("runId") == run_id:
                    return data.get("text") or data.get("response")
        return None

    async def _send_http(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict]) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "sessionId": session_id,
            "agentId": agent_id or self.agent_id,
            "text": text,
            "metadata": metadata or {},
        }

        def _post() -> requests.Response:
            return requests.post(
                f"{self.gateway_url}/api/message",
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("text") or data.get("response")
        return None

    async def listen(self) -> AsyncIterator[str]:
        """Poll for responses from IronClaw gateway."""
        if self.use_websocket:
            # For WebSocket mode, listening is handled by _send_ws
            if False:
                yield ""
            return
        
        logger.info("IronClawGateway polling: session=%s, interval=%.1fs", self._current_session_id, self.poll_interval_s)
        
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                
                try:
                    def _get() -> requests.Response:
                        return requests.get(
                            f"{self.gateway_url}/api/responses",
                            headers={
                                "Authorization": f"Bearer {self.token}",
                                "X-Session-Id": self._current_session_id,
                            },
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
                        
                        text = msg.get("text") or msg.get("response")
                        if not isinstance(text, str) or not text.strip():
                            continue
                        
                        msg_id = msg.get("id") or text
                        if msg_id not in self._yielded_messages:
                            self._yielded_messages.add(msg_id)
                            logger.info("← IronClaw: %s", text[:80])
                            yield text
                
                except requests.RequestException as exc:
                    logger.debug("IronClaw poll request failed: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("IronClawGateway polling error: %s", exc)


class MimiClawGateway(BaseGateway):
    provider = "mimiclaw"
    supports_listen = True

    def __init__(
        self,
        device_host: str,
        device_port: int = 18789,
        use_websocket: bool = True,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        timeout_s: int = 30,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.device_host = device_host
        self.device_port = device_port
        self.use_websocket = use_websocket
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_messages: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_messages.clear()
        
        if self.use_websocket:
            return await self._send_ws(text, session_id, metadata)
        if self.telegram_bot_token and self.telegram_chat_id:
            return await self._send_telegram(text)
        raise RuntimeError("MimiClaw requires WebSocket or Telegram configuration")

    async def _send_ws(self, text: str, session_id: str, metadata: Optional[dict]) -> Optional[str]:
        ws_url = f"ws://{self.device_host}:{self.device_port}"
        request_id = str(uuid4())
        payload = {
            "requestId": request_id,
            "sessionId": session_id,
            "userInput": text,
            "timestamp": int(time.time() * 1000),
            "metadata": metadata or {},
        }
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps(payload))
            while True:
                message = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                data = json.loads(message)
                if data.get("requestId") == request_id:
                    return data.get("response") or data.get("message")
        return None

    async def _send_telegram(self, text: str) -> Optional[str]:
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        def _post() -> requests.Response:
            return requests.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                json=payload,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        message_id = data.get("result", {}).get("message_id")
        return f"Message sent via Telegram (ID: {message_id})"

    async def listen(self) -> AsyncIterator[str]:
        """Poll for responses from MimiClaw gateway."""
        if self.use_websocket:
            # For WebSocket mode, listening is handled by _send_ws
            if False:
                yield ""
            return
        
        logger.info("MimiClawGateway polling: session=%s, interval=%.1fs", self._current_session_id, self.poll_interval_s)
        
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                
                try:
                    def _get() -> requests.Response:
                        return requests.get(
                            f"http://{self.device_host}:{self.device_port}/responses",
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
                        
                        text = msg.get("response") or msg.get("message")
                        if not isinstance(text, str) or not text.strip():
                            continue
                        
                        msg_id = msg.get("id") or text
                        if msg_id not in self._yielded_messages:
                            self._yielded_messages.add(msg_id)
                            logger.info("← MimiClaw: %s", text[:80])
                            yield text
                
                except requests.RequestException as exc:
                    logger.debug("MimiClaw poll request failed: %s", exc)
                    continue
        except Exception as exc:
            logger.warning("MimiClawGateway polling error: %s", exc)


class PicoClawGateway(BaseGateway):
    provider = "picoclaw"
    supports_listen = True

    def __init__(
        self,
        workspace_home: str,
        gateway_url: str = "",
        agent_id: str = "default",
        timeout_s: int = 30,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.workspace_home = Path(workspace_home)
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else ""
        self.agent_id = agent_id
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_message_ids: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_message_ids.clear()
        
        if self.gateway_url:
            try:
                return await self._send_http(text, session_id, agent_id, metadata)
            except Exception as exc:
                logger.warning("PicoClaw HTTP gateway failed (%s); falling back to file-based mode", exc)

        sessions_dir = self.workspace_home / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file = sessions_dir / f"{session_id}.jsonl"
        message_id = f"{int(time.time() * 1000)}-{uuid4()}"
        entry = {
            "id": message_id,
            "timestamp": int(time.time() * 1000),
            "type": "user",
            "content": text,
            "agent": agent_id or self.agent_id,
            "metadata": metadata or {},
        }
        await asyncio.to_thread(session_file.write_text, session_file.read_text("utf-8") + json.dumps(entry) + "\n" if session_file.exists() else json.dumps(entry) + "\n", "utf-8")
        return f"Message queued for {agent_id or self.agent_id} agent (PicoClaw file-based mode)"

    async def _send_http(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict]) -> Optional[str]:
        headers = {
            "Content-Type": "application/json",
            "X-Session-Id": session_id,
            "X-Agent-Id": agent_id or self.agent_id,
        }
        payload = {
            "text": text,
            "metadata": metadata or {},
            "timestamp": int(time.time() * 1000),
        }

        def _post() -> requests.Response:
            return requests.post(
                f"{self.gateway_url}/webhook",
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("text") or data.get("response")
        return None

    async def listen(self) -> AsyncIterator[str]:
        """Poll for agent responses from PicoClaw."""
        logger.info("PicoClawGateway listening for responses from session %s", self._current_session_id)
        sessions_dir = self.workspace_home / "sessions"
        
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                
                try:
                    session_file = sessions_dir / f"{self._current_session_id}.jsonl"
                    if not session_file.exists():
                        continue
                    
                    def read_messages() -> list:
                        lines = session_file.read_text("utf-8").strip().split("\n")
                        return [json.loads(line) for line in lines if line.strip()]
                    
                    messages = await asyncio.to_thread(read_messages)
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        
                        # Only yield assistant responses
                        if msg.get("type") != "assistant":
                            continue
                        
                        msg_id = msg.get("id")
                        if not msg_id or msg_id in self._yielded_message_ids:
                            continue
                        
                        text = msg.get("content")
                        if isinstance(text, str) and text.strip():
                            self._yielded_message_ids.add(msg_id)
                            logger.info("← PicoClaw: %s", text[:80])
                            yield text
                
                except (json.JSONDecodeError, ValueError, OSError):
                    continue
        except Exception as exc:
            logger.warning("PicoClawGateway listener error: %s", exc)


class NanoBotGateway(BaseGateway):
    provider = "nanobot"
    supports_listen = True

    def __init__(
        self,
        workspace_home: str,
        gateway_url: str = "",
        agent_id: str = "",
        timeout_s: int = 30,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.workspace_home = Path(workspace_home)
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else ""
        self.agent_id = agent_id
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._current_session_id: Optional[str] = None
        self._yielded_message_ids: set = set()

    async def send_message(self, text: str, session_id: str, agent_id: str, metadata: Optional[dict] = None) -> Optional[str]:
        self._current_session_id = session_id
        self._yielded_message_ids.clear()
        
        if self.gateway_url:
            try:
                return await self._send_http(text, session_id, metadata)
            except Exception as exc:
                logger.warning("NanoBot HTTP gateway failed (%s); falling back to file-based mode", exc)

        workspace_dir = self.workspace_home / "workspace"
        sessions_dir = workspace_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        channel_key = f"voice:{session_id}"
        session_file = sessions_dir / f"{channel_key}.json"
        message_id = f"{int(time.time() * 1000)}-{uuid4()}"

        def _update_session() -> None:
            if session_file.exists():
                session = json.loads(session_file.read_text("utf-8"))
            else:
                session = {
                    "channel": "voice",
                    "chat_id": session_id,
                    "messages": [],
                    "created_at": int(time.time() * 1000),
                    "updated_at": int(time.time() * 1000),
                }
            session["messages"].append({
                "id": message_id,
                "role": "user",
                "content": text,
                "timestamp": int(time.time() * 1000),
                "metadata": metadata or {},
            })
            session["updated_at"] = int(time.time() * 1000)
            session_file.write_text(json.dumps(session, indent=2), "utf-8")

        await asyncio.to_thread(_update_session)
        return "Message queued for NanoBot (file-based mode)"

    async def _send_http(self, text: str, session_id: str, metadata: Optional[dict]) -> Optional[str]:
        headers = {
            "Content-Type": "application/json",
            "X-Session-Id": session_id,
            "X-Channel": "voice",
        }
        payload = {
            "text": text,
            "chat_id": session_id,
            "channel": "voice",
            "metadata": metadata or {},
            "timestamp": int(time.time() * 1000),
        }

        def _post() -> requests.Response:
            return requests.post(
                f"{self.gateway_url}/message",
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )

        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("text") or data.get("response")
        return None

    async def listen(self) -> AsyncIterator[str]:
        """Poll for agent responses from NanoBot."""
        logger.info("NanoBotGateway listening for responses from session %s", self._current_session_id)
        workspace_dir = self.workspace_home / "workspace"
        sessions_dir = workspace_dir / "sessions"
        
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                
                try:
                    channel_key = f"voice:{self._current_session_id}"
                    session_file = sessions_dir / f"{channel_key}.json"
                    if not session_file.exists():
                        continue
                    
                    def read_session() -> dict:
                        return json.loads(session_file.read_text("utf-8"))
                    
                    session = await asyncio.to_thread(read_session)
                    messages = session.get("messages", [])
                    
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        
                        # Only yield assistant responses
                        if msg.get("role") != "assistant":
                            continue
                        
                        msg_id = msg.get("id")
                        if not msg_id or msg_id in self._yielded_message_ids:
                            continue
                        
                        text = msg.get("content")
                        if isinstance(text, str) and text.strip():
                            self._yielded_message_ids.add(msg_id)
                            logger.info("← NanoBot: %s", text[:80])
                            yield text
                
                except (json.JSONDecodeError, KeyError, OSError):
                    continue
        except Exception as exc:
            logger.warning("NanoBotGateway listener error: %s", exc)
