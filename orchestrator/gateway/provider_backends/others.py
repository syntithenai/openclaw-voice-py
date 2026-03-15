import asyncio
import json
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Optional
from uuid import uuid4

import requests
import websockets

from .core import BaseGateway

logger = logging.getLogger("orchestrator.gateway.providers")


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
                        msg_id = response_file.stem
                        if msg_id in self._pending_message_ids:
                            continue

                        try:
                            data = json.loads(response_file.read_text("utf-8"))
                            text = data.get("response") or data.get("message")
                            if isinstance(text, str) and text.strip():
                                logger.info("← TinyClaw: %s", text[:80])
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
        if self.use_websocket:
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
        if self.use_websocket:
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
