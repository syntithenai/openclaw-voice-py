#!/usr/bin/env python3
"""Selenium smoke test for embedded voice web UI chat/email flow."""

from __future__ import annotations

import asyncio
import contextlib
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from orchestrator.web import EmbeddedVoiceWebService
import orchestrator.web.realtime_service as realtime_service_module


HOST = "127.0.0.1"
UI_PORT = 18940
WS_PORT = 18941
TEST_EMAIL = "test@example.com"


def _selenium_ui_html(
        ws_port: int,
        mic_starts_disabled: bool = True,
        audio_authority: str = "native",
        server_instance_id: str = "",
) -> str:
        mic_disabled_js = "true" if mic_starts_disabled else "false"
        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>OpenClaw Voice</title>
</head>
<body>
    <div id=\"chatArea\"></div>
    <div id=\"chatComposerDock\">
        <form id=\"chatComposer\">
            <input id=\"chatInput\" autocomplete=\"off\" />
            <button id=\"chatSendBtn\" type=\"submit\">Send</button>
        </form>
    </div>
    <script>
        const WS_PORT = {ws_port};
        const MIC_STARTS_DISABLED = {mic_disabled_js};
        const S = {{ ws: null, wsConnected: false, chat: [], pendingChatSends: new Set(), nextClientMsgId: 1 }};

        function wsUrl() {{
            return (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.hostname + ':' + WS_PORT + '/ws';
        }}

        function sendAction(payload) {{
            if (S.ws && S.ws.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify(payload));
        }}

        function renderChat() {{
            const area = document.getElementById('chatArea');
            area.innerHTML = '';
            for (const m of S.chat) {{
                const d = document.createElement('div');
                d.textContent = String((m && m.text) || '');
                area.appendChild(d);
            }}
        }}

        function updateChatComposerState() {{
            const send = document.getElementById('chatSendBtn');
            if (!send) return;
            send.disabled = !S.wsConnected || S.pendingChatSends.size > 0;
        }}

        function handleMsg(msg) {{
            if (msg.type === 'state_snapshot') {{
                if (Array.isArray(msg.chat)) S.chat = msg.chat.slice();
                renderChat();
            }} else if (msg.type === 'chat_append' && msg.message) {{
                S.chat.push(msg.message);
                renderChat();
            }} else if (msg.type === 'chat_text_ack' && msg.client_msg_id) {{
                S.pendingChatSends.delete(String(msg.client_msg_id));
                updateChatComposerState();
            }}
        }}

        function connectWs() {{
            S.ws = new WebSocket(wsUrl());
            S.ws.onmessage = (evt) => {{
                if (typeof evt.data !== 'string') return;
                try {{ handleMsg(JSON.parse(evt.data)); }} catch (_) {{}}
            }};
            S.ws.onopen = () => {{
                S.wsConnected = true;
                updateChatComposerState();
                sendAction({{ type: 'ui_ready' }});
            }};
            S.ws.onclose = () => {{
                S.wsConnected = false;
                updateChatComposerState();
                setTimeout(connectWs, 1000);
            }};
        }}

        document.addEventListener('submit', (e) => {{
            const form = e.target;
            if (!form || form.id !== 'chatComposer') return;
            e.preventDefault();
            const input = document.getElementById('chatInput');
            const text = String((input && input.value) || '').trim();
            if (!text) return;
            const clientMsgId = 'c' + (S.nextClientMsgId++);
            S.pendingChatSends.add(clientMsgId);
            updateChatComposerState();
            sendAction({{ type: 'chat_text', text, client_msg_id: clientMsgId }});
            if (input) input.value = '';
        }});

        connectWs();
        renderChat();
        updateChatComposerState();
    </script>
</body>
</html>
"""


def run_selenium_flow() -> None:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,900")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(f"http://{HOST}:{UI_PORT}/#/home")

        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        wait.until(lambda d: d.execute_script("window.location.hash='#/home'; return true;"))
        wait.until(
            lambda d: d.execute_script(
                "const el=document.getElementById('chatComposerDock');"
                "return !!el && !el.classList.contains('hidden');"
            )
        )
        wait.until(
            lambda d: d.execute_script(
                "const btn=document.getElementById('chatSendBtn');"
                "return !!btn && !btn.disabled;"
            )
        )
        chat_input = wait.until(EC.presence_of_element_located((By.ID, "chatInput")))

        prompt = f"Please send an email to {TEST_EMAIL} saying hello from Selenium"
        try:
            chat_input.clear()
            chat_input.send_keys(prompt)
            send_btn = driver.find_element(By.ID, "chatSendBtn")
            send_btn.click()
        except Exception:
            chat_input.send_keys(prompt)
            chat_input.send_keys(Keys.ENTER)

        try:
            wait.until(EC.text_to_be_present_in_element((By.ID, "chatArea"), prompt))
            wait.until(EC.text_to_be_present_in_element((By.ID, "chatArea"), f"Email queued to {TEST_EMAIL}"))
        except TimeoutException as exc:
            page = driver.page_source
            logs = []
            with contextlib.suppress(Exception):
                logs = driver.get_log("browser")
            raise RuntimeError(
                "UI text assertion failed. "
                f"chatArea_present={bool(driver.find_elements(By.ID, 'chatArea'))}; "
                f"page_len={len(page)}; "
                f"console_logs={logs[:5]}"
            ) from exc
    finally:
        driver.quit()


async def main() -> None:
    realtime_service_module._build_ui_html = _selenium_ui_html
    service = EmbeddedVoiceWebService(host=HOST, ui_port=UI_PORT, ws_port=WS_PORT)

    async def on_chat_text(text: str, client_id: str) -> None:
        service.append_chat_message({"role": "user", "text": text, "source": "selenium"})
        if "email" in text.lower():
            service.append_chat_message(
                {
                    "role": "assistant",
                    "text": f"Email queued to {TEST_EMAIL}",
                    "source": "mock-email",
                }
            )
        else:
            service.append_chat_message({"role": "assistant", "text": "No email requested", "source": "mock-email"})

    service.set_action_handlers(on_chat_text=on_chat_text)

    await service.start()
    service.update_orchestrator_status(
        voice_state="listening",
        wake_state="awake",
        speech_active=False,
        tts_playing=False,
        mic_rms=0.0,
        queue_depth=0,
    )

    try:
        await asyncio.sleep(1.0)
        await asyncio.to_thread(run_selenium_flow)
        print("SELENIUM_SMOKE_OK")
    finally:
        with contextlib.suppress(Exception):
            await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
