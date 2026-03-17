# Embedded Realtime Voice UI

This orchestrator can expose a lightweight web UI + WebSocket bridge for continuous browser audio mode.

## What it provides

- Browser microphone capture with continuous streaming to the orchestrator over WebSocket
- Browser-side VU meter (local mic level)
- Realtime orchestrator status indicators:
  - sleep / awake
  - speech activity
  - hotword activity
  - TTS speaking state
- Orchestrator microphone VU meter
- Embeddable UI (e.g. iframe in OpenClaw web UI)

## Enable it

Set in your env profile (`.env`, `.env.docker`, or `.env.pi`):

- `WEB_UI_ENABLED=true`
- `WEB_UI_HOST=0.0.0.0`
- `WEB_UI_PORT=18910`
- `WEB_UI_WS_PORT=18911`
- `WEB_UI_STATUS_HZ=12`
- `WEB_UI_HOTWORD_ACTIVE_MS=2000`

Then restart the native orchestrator.

## URLs

- UI: `http://<host>:<WEB_UI_PORT>/`
- Health: `http://<host>:<WEB_UI_PORT>/health`
- WebSocket: `ws://<host>:<WEB_UI_WS_PORT>/ws`

## Embed example

Use in a third-party page:

`<iframe src="http://VOICE_HOST:18910/" style="width:100%;height:420px;border:0"></iframe>`

## WebSocket payloads

From browser UI to orchestrator:

- Text frame (level telemetry):
  - `{"type":"browser_audio_level","rms":0.031,"peak":0.22}`
- Binary frame (optional):
  - little-endian `int16` PCM chunk

From orchestrator to UI clients:

- `{"type":"status", "orchestrator": {...}, "browser_audio": {...}, "connections": {...}}`

`orchestrator` includes:

- `voice_state`
- `wake_state`
- `speech_active`
- `hotword_active`
- `tts_playing`
- `mic_rms`
- `queue_depth`

## Chat UI smoke checklist (thinking/tool timeline)

Use this checklist after UI changes in `orchestrator/web/realtime_service.py`.

### Preconditions

- Orchestrator UI is reachable at `http://<host>:<WEB_UI_PORT>/`
- A chat session is connected and can call tools

### Scenario A: waiting icon while still streaming

1. Send a prompt that triggers at least one long-running tool call (for example process poll/log follow-up).
2. Open the **Thinking** details block for that request.
3. While the request is still active, confirm the **Thinking summary row** shows a spinner icon.
4. Confirm the block also shows `waiting…` until the run reaches terminal state.

Expected: spinner is visible during active streaming/waiting and disappears when done.

### Scenario B: exec command preview max two lines

1. Trigger an `exec`/terminal-style tool call with a multi-line command.
2. In the tool row under the block header, inspect the inline command preview.
3. Expand `payload`/`results` details for the same tool row.

Expected:

- Inline preview under the header shows at most 2 lines.
- Full command/output remains visible in expandable details.

### Scenario C: transient connection lifecycle errors do not flip whole block to failure

1. Trigger a run where tool work succeeds/continues but lifecycle emits a transient connection message (for example `Connection error.`).
2. Observe the run-level status row in Thinking.

Expected:

- The block does **not** immediately become `✕ completed with errors` from transient connection noise alone.
- `completed with errors` appears only for hard lifecycle errors/timeouts or real tool failure.

### Optional quick contract check (source-level)

Run from `openclaw-voice`:

`python3.12 - <<'PY'`
`from test_realtime_ui_file_path_contract import (`
`    test_tool_request_extracts_snake_case_file_path,`
`    test_thinking_block_shows_waiting_icon_in_summary,`
`    test_exec_preview_clamped_to_two_lines,`
`    test_transient_lifecycle_errors_not_auto_terminal_failure,`
`)`
`test_tool_request_extracts_snake_case_file_path()`
`test_thinking_block_shows_waiting_icon_in_summary()`
`test_exec_preview_clamped_to_two_lines()`
`test_transient_lifecycle_errors_not_auto_terminal_failure()`
`print('ok')`
`PY`
