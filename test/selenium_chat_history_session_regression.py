#!/usr/bin/env python3
"""Selenium regression for chat history session editing and first-message promotion."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.web import EmbeddedVoiceWebService


HOST = "127.0.0.1"
UI_PORT = 18942
WS_PORT = 18943
HISTORIC_THREAD_ID = "historic-thread-1"
HISTORIC_TITLE = "Original session"
NEW_SESSION_FIRST_MESSAGE = "Brand new session title"
EDITED_HISTORIC_MESSAGE = "Edit this existing session"


def _chrome_options() -> Options:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,900")

    configured_binary = os.getenv("SELENIUM_CHROME_BINARY")
    candidate_binaries = [
        configured_binary,
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/google-chrome",
        "/snap/bin/chromium",
        "/usr/bin/chromium",
    ]
    for binary_path in candidate_binaries:
        if binary_path and os.path.exists(binary_path):
            options.binary_location = binary_path
            break
    return options


def _history_titles(driver: webdriver.Chrome) -> list[str]:
    return driver.execute_script(
        "return [...document.querySelectorAll('[data-action=\"chat-select\"] .text-sm')]"
        ".map(el => String(el.textContent || '').trim())"
        ".filter(Boolean);"
    )


def _history_count(driver: webdriver.Chrome) -> int:
    return int(
        driver.execute_script(
            "return document.querySelectorAll('[data-action=\"chat-select\"]').length;"
        )
    )


def run_selenium_flow() -> None:
    driver = webdriver.Chrome(options=_chrome_options())
    try:
        driver.get(f"http://{HOST}:{UI_PORT}/#/home")
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        wait.until(EC.presence_of_element_located((By.ID, "chatInput")))
        wait.until(
            lambda d: d.execute_script(
                "const btn=document.getElementById('chatSendBtn'); return !!btn && !btn.disabled;"
            )
        )
        wait.until(lambda d: HISTORIC_TITLE in _history_titles(d))

        historic_row = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f'[data-action="chat-select"][data-thread-id="{HISTORIC_THREAD_ID}"]'))
        )
        historic_row.click()
        wait.until(EC.text_to_be_present_in_element((By.ID, "chatArea"), HISTORIC_TITLE))

        initial_count = _history_count(driver)
        chat_input = driver.find_element(By.ID, "chatInput")
        chat_input.clear()
        chat_input.send_keys(EDITED_HISTORIC_MESSAGE)
        try:
            driver.find_element(By.ID, "chatSendBtn").click()
        except Exception:
            chat_input.send_keys(Keys.ENTER)

        wait.until(EC.text_to_be_present_in_element((By.ID, "chatArea"), EDITED_HISTORIC_MESSAGE))
        wait.until(lambda d: _history_count(d) == initial_count)
        wait.until(lambda d: _history_titles(d).count(HISTORIC_TITLE) == 1)

        new_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-action="chat-new"]')))
        new_btn.click()
        wait.until(lambda d: NEW_SESSION_FIRST_MESSAGE not in _history_titles(d))

        chat_input = wait.until(EC.presence_of_element_located((By.ID, "chatInput")))
        chat_input.clear()
        chat_input.send_keys(NEW_SESSION_FIRST_MESSAGE)
        try:
            driver.find_element(By.ID, "chatSendBtn").click()
        except Exception:
            chat_input.send_keys(Keys.ENTER)

        wait.until(EC.text_to_be_present_in_element((By.ID, "chatArea"), NEW_SESSION_FIRST_MESSAGE))
        wait.until(lambda d: _history_count(d) == initial_count + 1)
        wait.until(lambda d: len(_history_titles(d)) >= 1 and _history_titles(d)[0] == NEW_SESSION_FIRST_MESSAGE)
    except TimeoutException as exc:
        page = driver.page_source
        raise RuntimeError(f"chat history selenium regression failed; page_len={len(page)}") from exc
    finally:
        driver.quit()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="openclaw-chat-history-test-") as tmp_dir:
        persist_path = str(Path(tmp_dir) / "chat_state.json")
        service = EmbeddedVoiceWebService(host=HOST, ui_port=UI_PORT, ws_port=WS_PORT, chat_persist_path=persist_path)
        service._chat_threads = [
            {
                "id": HISTORIC_THREAD_ID,
                "title": HISTORIC_TITLE,
                "messages": [
                    {"id": 1, "role": "user", "text": HISTORIC_TITLE, "ts": time.time() - 120},
                    {"id": 2, "role": "assistant", "text": "Historic assistant reply", "ts": time.time() - 119},
                ],
                "created_ts": time.time() - 120,
                "updated_ts": time.time() - 119,
            }
        ]

        async def on_chat_new(client_id: str) -> None:
            service.start_new_chat()

        async def on_chat_text(text: str, client_id: str) -> None:
            service.append_chat_message({"role": "user", "text": text, "source": "selenium-regression"})
            service.append_chat_message(
                {
                    "role": "assistant",
                    "text": f"Assistant reply for: {text}",
                    "source": "selenium-regression",
                }
            )

        service.set_action_handlers(on_chat_new=on_chat_new, on_chat_text=on_chat_text)

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
            print("SELENIUM_CHAT_HISTORY_REGRESSION_OK")
        finally:
            with contextlib.suppress(Exception):
                await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
