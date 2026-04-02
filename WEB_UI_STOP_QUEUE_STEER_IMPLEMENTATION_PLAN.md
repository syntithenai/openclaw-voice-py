# OpenClaw Voice Web UI: Stop, Queue, Steer, and Sandbox Task Visibility Plan

## Intent
This plan covers only the openclaw-voice orchestrator web UI stack.

Goals:
- Add a Stop control that applies only to the current in-flight streaming interaction.
- Add Queue versus Steer message handling in the chat composer.
- Enable verbose and reasoning modes from the voice orchestrator UI and apply them when new upstream sessions are started.
- Implement the requested queue semantics:
  - If send mode is Steer and a message is queued while a steer is pending, append new text to the last queued steer message.
  - If send mode is Queue, allow multiple queued messages.
- Show queued items in the UI with an up arrow action that immediately steers the current request with that queued item and removes it from queue.
- Improve visibility of activity, failure, and completion states.
- Ensure lifecycle-rich information is either captured in the debug log and/or surfaced as interim chat messages.
- Add a sandbox task monitor strip to the right of the alarms and timers bar.
- Show sandbox task success output and error states clearly in the monitor strip.
- Clicking a running sandbox task opens detailed information with live logs.
- Track docker sandbox exec task lifecycle with per-container and per-exec identifiers.
- Include subagent runs in the same timer-bar monitor strip and expose real-time thinking stream in an info view.

No implementation is performed by this document.

## Current Baseline
- Chat composer and send state are handled in [orchestrator/web/static/index.html](orchestrator/web/static/index.html), [orchestrator/web/static/app-core.js](orchestrator/web/static/app-core.js), and [orchestrator/web/static/app-events.js](orchestrator/web/static/app-events.js).
- Incoming chat updates are handled in [orchestrator/web/static/app-ws.js](orchestrator/web/static/app-ws.js).
- Grouped thinking and waiting indicators are rendered in [orchestrator/web/static/app-events.js](orchestrator/web/static/app-events.js).
- Websocket action routing and chat acknowledgements are in [orchestrator/web/realtime_service.py](orchestrator/web/realtime_service.py).
- Chat text handler wiring and transcript debounce pipeline are in [orchestrator/main.py](orchestrator/main.py).
- Timer and alarm bar rendering currently lives in [orchestrator/web/static/app-render.js](orchestrator/web/static/app-render.js) via renderTimerBar.
- The orchestrator already supports a reasoning directive interception path, but it is not yet represented as first-class UI session controls in the voice web UI.

## Behavioral Contract

### 1) Stop Scope
- Stop is visible only when there is an in-flight active run for the currently selected active chat stream.
- Stop affects only the current streaming interaction.
- Failed and long-running historical interactions are not stop targets.
- New user input always supersedes stale in-flight work.

### 2) Send Mode
- Add chat send modes:
  - Queue mode: enqueue new message, preserve current run.
  - Steer mode: stop current run, then resend using current interaction plus queued steer text.

### 3) Queue Semantics (Requested)
- Queue mode:
  - Allow many queue entries.
  - Entries are processed FIFO after current run reaches terminal state.
- Steer mode:
  - If a steer sequence is already pending, append incoming text to the last queued steer message (single coalescing steer tail).
  - If no steer message exists yet, create one queued steer entry.
- Queued list UI:
  - Each queued item has an up arrow action.
  - Up arrow immediately triggers steer-now with that item.
  - The selected queue item is removed from queue immediately on local optimistic action, then confirmed by server.

### 4) Activity and Failure Visibility
- Explicit run status chip at composer level: idle, streaming, waiting_tool, stopping, failed, timed_out, completed.
- Explicit terminal reason display on completion.
- Always clear waiting indicators on terminal states.
- Display run elapsed time while active.
- Surface actionable error text when stop fails or run errors.

### 5) Sandbox Task Visibility and Interaction
- Display sandbox tasks inline to the right of alarms and timers bar, preserving timer/alarm actions.
- Each sandbox task pill shows:
	- short label
	- state icon
	- live indicator for running tasks
	- terminal indicator for success or error
- Clicking a running sandbox task opens a detail panel with:
	- task metadata
	- current status and elapsed time
	- live log stream that updates while task runs
- Clicking completed task opens the same detail panel with final output and preserved logs.
- Error tasks must be visually distinct and include concise failure summary in list and full message in detail panel.

### 8) Docker Sandbox Exec Task Tracking
- Sandbox monitor must track actual docker-hosted exec activity, not only high-level orchestrator task summaries.
- Each tracked exec task includes:
	- container id and container name
	- docker exec id or synthetic exec correlation id
	- command preview
	- started, running, completed, failed, cancelled lifecycle
	- exit code and termination reason when available
- Log view must distinguish stdout and stderr streams and preserve ordering with sequence numbers.
- If docker task metadata is unavailable at source, orchestrator must emit synthetic ids and explicit metadata-quality flags.

### 9) Subagent Visibility in Timer Bar
- Timer bar right-side monitor includes subagent runs as first-class task chips alongside sandbox tasks.
- Subagent chip states:
	- queued
	- running
	- waiting_input
	- completed
	- failed
	- cancelled
- Clicking a subagent chip opens info view with:
	- current step and status
	- real-time thinking stream updates
	- latest tool actions and outcomes
	- terminal summary and error details when applicable
- Thinking stream visibility follows lifecycle detail policy and can be independently collapsed in panel UI.

### 6) Verbose and Reasoning Session Controls
- Voice orchestrator UI exposes explicit controls for verbose and reasoning levels.
- Selected values are persisted in voice UI state and sent to orchestrator settings handlers.
- On new upstream session creation/reset from voice orchestrator flows, those settings are applied to the upstream session before first user dispatch whenever possible.
- This setting propagation can affect OpenClaw Web UI behavior for newly started sessions because session-level flags are shared server-side.

### 7) Lifecycle Detail Surfacing Contract
- Lifecycle detail signals include reasoning segments, lifecycle phase events, and tool phase summaries.
- At least one visibility channel must be available for each signal:
	- debug log capture path, and/or
	- interim chat messages path.
- Provide a UI-level policy toggle:
	- chat_only
	- debug_log_only
	- both
- Default behavior for this rollout: both.

## Data Model Additions

### Client State (browser)
Edit [orchestrator/web/static/app-core.js](orchestrator/web/static/app-core.js):
- Add fields to S:
  - chatSendMode: queue or steer
  - chatRunState: idle, streaming, waiting_tool, stopping, failed, timed_out, completed
  - chatActiveRequestId: string
  - chatQueuedItems: array of queued objects
  - chatQueueSeq: number for local ids
  - chatStopPending: boolean
  - chatStatusText: string
  - chatLastActivityTs: number
  - chatLastTerminal: object with reason and ts

	- chatVerboseLevel: off, on, full
	- chatReasoningLevel: off, on, stream
	- chatLifecycleDetailPolicy: chat_only, debug_log_only, both
	- chatInterimLifecycleEnabled: boolean
	- debugLogLifecycleEnabled: boolean
	- chatLifecycleInterimBuffer: array of interim lifecycle entries

	- sandboxTasks: array of sandbox task summaries
	- sandboxTaskById: map for quick updates
	- sandboxTaskPanelOpen: boolean
	- sandboxTaskPanelId: string
	- sandboxTaskLogsById: map of log lines
	- sandboxTaskLogCursorById: map for incremental live log fetch
	- sandboxTaskLastSyncTs: number

	- subagentTasks: array of subagent task summaries
	- subagentTaskById: map for quick updates
	- subagentTaskPanelOpen: boolean
	- subagentTaskPanelId: string
	- subagentThinkingById: map of streamed thinking segments
	- subagentThinkingCursorById: map for incremental stream sync
	- taskStripFilter: all, sandbox, subagent

### Server Session State (web service)
Edit [orchestrator/web/realtime_service.py](orchestrator/web/realtime_service.py):
- Add per-client or shared active run fields:
  - active_request_id
  - active_run_state
  - queued_messages list
  - pending_steer_message aggregator
  - last_terminal_state
	- sandbox_tasks summary list
	- sandbox_task_logs ring buffers by task id

	- sandbox_exec_index keyed by container_id and exec_id
	- subagent_task_index keyed by subagent_run_id
	- subagent_thinking_logs ring buffers by subagent_run_id

	- effective_verbose_level
	- effective_reasoning_level
	- lifecycle_detail_policy
	- lifecycle_debug_log ring buffer with bounded retention
- Include these in state snapshot payload and dedicated queue/run updates.

### Orchestrator Runtime State
Edit [orchestrator/main.py](orchestrator/main.py):
- Add orchestration controls:
  - active request token/run id
  - cancel event or task handle for current processing pipeline
  - queue for deferred UI messages
  - steer aggregation buffer when steer mode is active
	- sandbox task observer callbacks that emit task start, progress, output, success, and error states to web service

	- docker sandbox exec observer callbacks that emit container-scoped exec lifecycle and stream events
	- subagent observer callbacks that emit run lifecycle and incremental thinking segments

	- session settings apply hook for verbose/reasoning at session bootstrap and reset
	- lifecycle detail fanout helper that can emit interim chat rows and debug log entries from one normalized event payload

## Detailed File Edit Plan

### A) UI Markup and Controls
Edit [orchestrator/web/static/index.html](orchestrator/web/static/index.html):
1. Extend chat composer dock to include:
	- Send mode segmented control: Queue and Steer.
	- Stop button region (hidden by default; shown while in-flight).
	- Run status row with state chip and elapsed timer.
	- Queue panel under composer listing queued items.
2. For each queue item row include:
	- Item text preview.
	- Up arrow button with data-action chat-queue-steer-now.
	- Remove button with data-action chat-queue-remove.
3. Add ids for state updates:
	- chatModeQueueBtn, chatModeSteerBtn
	- chatStopBtn
	- chatRunStatusChip, chatRunStatusText, chatRunElapsed
	- chatQueueList, chatQueueEmpty
4. Extend timer/alarm dock area (currently timerBar region) to include sandbox task strip on the right:
	- sandboxTaskStrip
	- sandboxTaskList
	- sandboxTaskEmpty
	- sandboxTaskPanel
	- sandboxTaskPanelClose
	- sandboxTaskPanelMeta
	- sandboxTaskPanelLog
	- sandboxTaskPanelStatus
5. Extend the same monitor strip to include subagent chips and shared task filter tabs:
	- taskStripFilterAll
	- taskStripFilterSandbox
	- taskStripFilterSubagent
	- subagentTaskList
	- subagentTaskEmpty
	- subagentTaskPanel
	- subagentTaskPanelClose
	- subagentTaskPanelMeta
	- subagentTaskPanelThinking
	- subagentTaskPanelStatus
6. Add session diagnostics control group in chat settings area:
	- verbose level selector
	- reasoning level selector
	- lifecycle detail policy selector (chat/debug/both)
	- interim lifecycle visibility toggle

### B) Core UI State and Helpers
Edit [orchestrator/web/static/app-core.js](orchestrator/web/static/app-core.js):
1. Initialize new S fields listed above.
2. Add helper functions:
	- isChatRunInFlight
	- isChatRunTerminal
	- setChatRunState
	- enqueueChatItemQueueMode
	- enqueueChatItemSteerMode (append behavior)
	- removeQueuedItemById
	- popQueuedItemById
	- formatRunElapsedMs
3. Expand updateChatComposerState to:
	- show Stop only for in-flight states
	- keep input enabled while in-flight (to allow queueing)
	- disable only when websocket disconnected or hard stopping requires a brief lock
	- update status chip text and color
4. Add renderChatQueueList helper invoked by page render and websocket updates.
5. Add sandbox helpers:
	- upsertSandboxTaskSummary
	- setSandboxTaskLogs
	- appendSandboxTaskLogs
	- renderSandboxTaskStrip
	- openSandboxTaskPanel
	- closeSandboxTaskPanel
	- updateSandboxTaskPanel
6. Add subagent monitor helpers:
	- upsertSubagentTaskSummary
	- appendSubagentThinking
	- renderSubagentTaskStrip
	- openSubagentTaskPanel
	- closeSubagentTaskPanel
	- updateSubagentTaskPanel
7. Add unified task strip helpers:
	- renderTaskStripMerged
	- sortTaskStripByRecencyAndState
	- formatTaskOriginBadge sandbox or subagent
8. Add lifecycle detail helpers:
	- pushLifecycleInterimEntry
	- appendLifecycleDebugEntry
	- renderLifecycleInterimRows
	- pruneLifecycleInterimBuffer

### C) Event Handling
Edit [orchestrator/web/static/app-events.js](orchestrator/web/static/app-events.js):
1. Replace submit behavior:
	- If no in-flight run: send immediately as chat_text with mode metadata.
	- If in-flight and mode is Queue: enqueue new queue item and render queue list.
	- If in-flight and mode is Steer: enqueue via steer coalescing rule (append to last steer item).
2. Add click handlers:
	- chat-mode-set-queue
	- chat-mode-set-steer
	- chat-stop
	- chat-queue-remove
	- chat-queue-steer-now
3. chat-stop action:
	- optimistic state to stopping
	- send websocket action chat_stop with active_request_id
4. chat-queue-steer-now action:
	- remove selected queue item optimistically
	- send chat_steer_now with item id and text
5. Ensure updateChatComposerState and renderChatQueueList are called after each action.
6. Preserve current chat thread behavior and avoid affecting music queue handlers.
7. Add sandbox task interactions:
	- click handler for sandbox task pill to open detail panel
	- click handler for panel close
	- optional refresh action for logs
	- polling or request trigger for live logs while panel is open and task is running
8. Add subagent task interactions:
	- click handler for subagent chip opens subagent info view
	- live thinking stream subscribe while panel open
	- optional pause/resume auto-scroll for high-rate thinking tokens
9. Add change handlers for verbose/reasoning/policy controls and dispatch setting updates over websocket.

### D) Websocket Message Handling
Edit [orchestrator/web/static/app-ws.js](orchestrator/web/static/app-ws.js):
1. Add handlers for new payload types:
	- chat_run_state
	- chat_stop_ack
	- chat_queue_update
	- chat_steer_ack
	- chat_steer_error
2. On chat_append and chat_update:
	- update chatLastActivityTs
	- if assistant stream, set streaming
	- if final assistant segment detected, transition to completed
3. On error or timeout events:
	- set failed or timed_out state
	- clear waiting spinner conditions
4. On terminal transitions:
	- if queue has items and mode is Queue, request next queued dispatch from server or send local dequeue action.
5. Keep existing partial stream patch logic intact while adding state transition hooks.
6. Add handlers for sandbox events:
	- sandbox_task_update (summary updates)
	- sandbox_task_log_append (incremental live logs)
	- sandbox_task_snapshot (full list bootstrap)
	- sandbox_task_error (transport-level or task-level failure payload)
7. Ensure UI updates do not force full page rerender on each log line; patch strip and panel in place.
8. Add handlers for docker exec-specific events:
	- sandbox_exec_update
	- sandbox_exec_log_append
	- sandbox_exec_terminal
9. Add handlers for subagent events:
	- subagent_task_update
	- subagent_thinking_append
	- subagent_task_terminal
10. Add handlers for lifecycle detail channels:
	- chat_lifecycle_interim
	- chat_debug_log_append
11. Route interim messages into chat when policy enables chat visibility.
12. Route debug entries into debug panel/log buffer when policy enables debug capture.

### E) Websocket Action Router and Snapshot
Edit [orchestrator/web/realtime_service.py](orchestrator/web/realtime_service.py):
1. Extend handler registrations:
	- on_chat_stop
	- on_chat_steer_now
	- on_chat_queue_update optional callback
2. In message action switch add new msg_type handlers:
	- chat_stop
	- chat_steer_now
	- chat_queue_set_mode optional if server-owned mode is desired
3. Emit acknowledgements:
	- chat_stop_ack with request id and result
	- chat_steer_ack or chat_steer_error
4. Add queue and run state to:
	- state_snapshot payload
	- incremental broadcast updates
5. Keep current chat_text_ack path for immediate sends.
6. Add sandbox action handlers and broadcasts:
	- on_sandbox_task_logs optional callback
	- client action sandbox_task_logs_get for detail panel fetch or resume
	- include sandbox task summaries in state_snapshot
	- broadcast incremental sandbox_task_update and sandbox_task_log_append events
7. Add bounded retention policy for in-memory logs and explicit truncation metadata.
8. Add docker exec task actions and broadcasts:
	- include docker-exec-aware sandbox payload fields in state_snapshot
	- broadcast sandbox_exec_update and sandbox_exec_log_append with sequence ids
9. Add subagent actions and broadcasts:
	- optional action subagent_task_thinking_get for backfill
	- include subagent task summaries in state_snapshot
	- broadcast subagent_task_update and subagent_thinking_append
10. Add settings action handlers:
	- chat_session_diagnostics_set
	- chat_lifecycle_policy_set
11. Include effective verbose and reasoning settings in state_snapshot for initial UI hydration.

### F) Orchestrator Chat Control and Cancellation
Edit [orchestrator/main.py](orchestrator/main.py):
1. Add UI callback implementations:
	- _ui_chat_stop(client_id, request_id)
	- _ui_chat_steer_now(text, queue_item_id, client_id)
2. Register new callbacks through web_service.set_action_handlers.
3. Add run controller around send_debounced_transcripts:
	- current run token
	- cancel signal or task cancellation for active run
	- terminal callback to UI state publisher
4. Stop behavior:
	- cancel debounce task if pending
	- cancel active processing task if running
	- increment request guard token so stale responses are dropped
	- broadcast aborted or stopped state
5. Queue behavior:
	- maintain FIFO queue for Queue mode
	- after terminal, dispatch next queued item automatically
6. Steer behavior:
	- stop current run
	- compose resend text from selected queued entry
	- dispatch immediate send_debounced_transcripts
7. Maintain existing stale reply protections using request id checks, and extend them to new queue and steer flows.
8. Add sandbox execution instrumentation bridge:
	- publish task lifecycle: started, running, completed, failed
	- publish output chunks and stderr chunks as task logs
	- include success output excerpt and error summary for list-level visibility
	- assign stable task ids and timestamps for ordering
9. Add docker exec lifecycle instrumentation:
	- resolve container id and name for each sandboxed exec
	- capture exec correlation id and emit start, output, end, error
	- emit stdout and stderr channels separately with ordered sequence numbers
10. Add subagent lifecycle and thinking instrumentation:
	- emit subagent run state transitions and heartbeat timestamps
	- emit incremental thinking segments for active runs
	- emit terminal summary with tool/error rollup
11. Apply verbose/reasoning settings to upstream session on new session startup/reset path before first dispatch:
	- invoke gateway session patch with reasoningLevel and verboseLevel
	- confirm and publish resulting effective values to voice UI state
12. Build unified lifecycle detail emitter:
	- normalize lifecycle/tool/reasoning events
	- write bounded entries into orchestrator debug log stream
	- optionally emit interim assistant/system chat rows

### G) Consistent Waiting and Terminal Interpretation
Edit [orchestrator/web/static/app-events.js](orchestrator/web/static/app-events.js):
1. In context_group waiting logic, bind waiting strictly to active request id.
2. Add explicit terminal detection fallback:
	- lifecycle end
	- assistant final segment
	- explicit error or timeout
3. Ensure waiting spinner is always removed after terminal.
4. Add small status summary row in context group for failed and timed out states.

### H) Optional Styling Enhancements
Edit [orchestrator/web/static/index.html](orchestrator/web/static/index.html):
1. Add lightweight utility classes for:
	- run status chip variants
	- queue list item layout
	- up arrow action button emphasis
	- sandbox task pill variants: running, success, error
	- sandbox detail panel layout and log console styling

## New Websocket Actions and Events

### Client to Server actions
- chat_stop:
  - request_id
  - source client id implicit
- chat_steer_now:
  - queue_item_id
  - text
- chat_queue_update optional if server-authoritative queue is used.
- chat_session_diagnostics_set:
	- verbose_level
	- reasoning_level
- chat_lifecycle_policy_set:
	- policy chat_only, debug_log_only, both
	- interim_enabled boolean

### Server to Client events
- chat_run_state:
  - request_id
  - state
  - reason optional
  - elapsed_ms optional
- chat_stop_ack:
  - request_id
  - ok
  - error optional
- chat_queue_update:
  - queued_items array
- chat_steer_ack:
  - queue_item_id
  - request_id
- chat_lifecycle_interim:
	- request_id
	- phase
	- name
	- details
- chat_debug_log_append:
	- request_id optional
	- ts
	- level
	- event
	- payload
- sandbox_task_snapshot:
	- tasks array
- sandbox_task_update:
	- task summary payload
- sandbox_task_log_append:
	- task_id
	- lines array
	- cursor
- sandbox_task_error:
	- task_id optional
	- error
- sandbox_exec_update:
	- task_id
	- container_id
	- container_name
	- exec_id
	- status
	- command
	- started_ts
	- ended_ts optional
	- exit_code optional
- sandbox_exec_log_append:
	- task_id
	- exec_id
	- seq
	- stream stdout or stderr
	- lines
- subagent_task_update:
	- run_id
	- label
	- status
	- started_ts
	- ended_ts optional
	- step optional
- subagent_thinking_append:
	- run_id
	- seq
	- text_delta
- subagent_task_terminal:
	- run_id
	- status
	- summary
	- error optional

## Queue Item Shape
- id
- text
- mode_origin queue or steer
- created_ts
- updated_ts

## Sandbox Task Shape
- id
- label
- status running, success, error
- started_ts
- ended_ts optional
- elapsed_ms
- success_output_excerpt optional
- error_summary optional
- source sandbox name or workspace lane
- has_live_logs boolean

## Subagent Task Shape
- run_id
- label
- status queued, running, waiting_input, completed, failed, cancelled
- started_ts
- ended_ts optional
- step optional
- thinking_preview optional
- summary optional
- error_summary optional

## Docker Exec Task Shape
- task_id
- container_id
- container_name
- exec_id
- command
- status started, running, completed, failed, cancelled
- started_ts
- ended_ts optional
- exit_code optional
- metadata_quality native or synthetic

## Required Semantics Validation
1. Steer mode append behavior:
	- Start a long stream.
	- Submit message A in steer mode during stream.
	- Submit message B in steer mode before stop completes.
	- Queue should contain one steer item with combined text.
2. Queue mode multi-item behavior:
	- During stream, submit three messages in queue mode.
	- Queue should contain three separate items in order.
3. Up arrow steer-now behavior:
	- Click up arrow on second queue item while stream active.
	- Current run is stopped.
	- Selected item is removed.
	- Selected item is dispatched immediately.
4. Stop scoping:
	- Stop never aborts historical failed runs.
	- Stop targets only active_request_id.
5. Visibility:
	- Failed and timeout states show explicit terminal reason.
	- Waiting icon is cleared on every terminal path.
6. Sandbox strip and panel behavior:
	- Running sandbox tasks appear to the right of timer/alarm chips.
	- Success and error tasks are visibly distinct and include output summary.
	- Clicking a running task opens detail panel with live updating logs.
	- Clicking completed task opens detail panel with terminal output and log history.
7. Docker sandbox exec observability:
	- Each running docker exec appears with container and exec identifiers.
	- Stdout and stderr are visible in ordered live stream in detail panel.
	- Terminal state includes exit code when available.
8. Subagent visibility and thinking:
	- Subagents appear in timer-bar task strip with real-time state updates.
	- Clicking subagent opens info view with real-time thinking stream.
	- Subagent terminal summary/error is visible after completion.

## Completion Detection Strategy and Accuracy Targets

### Detection Objective
- Improve message-cycle completion detection from heuristic-only interpretation to state-driven interpretation with heuristic fallback.
- Use redundant lifecycle evidence to classify each request as one of:
	- in_progress
	- terminal_success
	- terminal_error
	- terminal_timeout
	- terminal_aborted

### Terminal Signal Priority
1. Strong terminal signals (authoritative):
	- chat_run_state with terminal state
	- lifecycle phase end or result for active request_id
	- explicit stop acknowledgement for active request_id
2. Medium terminal signals (corroborating):
	- assistant final segment with matching request_id
	- all tool phases terminal with no active lifecycle waiting marker
3. Fallback terminal signals (heuristic):
	- no new stream/lifecycle/tool deltas for quiet-window threshold after last final candidate

### Multi-Signal Completion Rule
- Mark finished only when:
	- at least one strong terminal signal is present, or
	- two medium signals are present and no contradictory in-progress signals exist, or
	- fallback quiet-window path triggers and no in-progress signals remain.
- Mark not finished when any of the following are present:
	- active stream deltas for request_id
	- waiting_tool phase without terminal override
	- non-terminal chat_run_state

### Contradiction Resolution
- If a late stale terminal frame arrives for non-active request_id, ignore for active completion state.
- If terminal and in-progress signals conflict for same request_id, prefer newest timestamped event and preserve prior state in debug log.
- Stop and steer transitions set a superseded marker so delayed old frames cannot reopen completion.

### Expected Accuracy Targets
- Baseline (pre-change) observed by heuristic interpretation only: approximate 75-85% correct completion classification in noisy multi-phase runs.
- Target after rollout with policy=both (interim chat + debug log): 92-97% correct completion classification.
- Minimum rollout gate target for general enablement: >=92% correct classification in contract and replay suites.

### Telemetry and Audit Fields
- Add completion audit record per request_id:
	- final_classification
	- winning_signal_type
	- signal_count_by_tier
	- contradiction_count
	- stale_frame_dropped_count
	- classification_latency_ms from first user dispatch to terminal classification
- Persist records in debug log stream and optional diagnostics export.

## Reliability Hardening for Remaining Limits

### A) Transport Loss and Out-of-Order Frames
- Add per-request monotonic stream_seq numbers to lifecycle/tool/stream events.
- Drop out-of-order frames on client when stream_seq is older than last accepted sequence for that request_id.
- Add server-side replay buffer keyed by request_id with bounded retention.
- Add client acknowledgements:
	- chat_stream_ack with request_id and last_seq.
	- server resends missing frames after reconnect or detected sequence gap.
- Add terminal durability:
	- persist terminal record per request_id for short retention window.
	- expose chat_request_reconcile action returning authoritative final snapshot.
- On detected gap near terminal boundary, switch classifier to reconcile mode before declaring completion.

### B) Provider Variance and Weak Lifecycle Semantics
- Normalize provider event shapes into a strict internal schema:
	- lifecycle_start, lifecycle_phase, lifecycle_end, lifecycle_error, lifecycle_timeout
	- tool_start, tool_result, tool_end, tool_error
	- assistant_stream, assistant_final
- Add provider adapter profiles with expected signal quality and fallback behavior.
- Synthesize missing terminal semantics when provider output is weak:
	- final without end can emit synthetic lifecycle_end.
	- stream terminated without final can emit lifecycle_timeout or lifecycle_error by adapter policy.
- Add provider-specific timeout and failure mappers to normalized terminal reason codes.

### C) Cancellation Races (Stop and Steer Supersession)
- Introduce generation fencing:
	- increment request_generation on stop/steer supersession.
	- reject any frame with lower generation than active generation for that session lane.
- Add explicit cancel barrier states in run-state machine:
	- stopping_pending, stopping_confirmed, superseded.
- Enforce single terminal winner per request_id + generation combination.
- Make terminal transitions idempotent:
	- first valid terminal commits state.
	- late duplicates/stale terminals are logged but do not mutate active state.
- Add superseded_by metadata so stale terminal records are traceable in debug logs.

### D) New Data Contract Additions
- Event metadata fields:
	- request_id
	- request_generation
	- stream_seq
	- event_ts
- New actions/events:
	- chat_stream_ack
	- chat_stream_replay
	- chat_request_reconcile
	- chat_reconcile_snapshot

### E) Operational Guardrails
- Keep replay buffer and debug log retention bounded by count and age.
- Track sequence gap rate and reconcile rate telemetry.
- If reconcile rate exceeds threshold, auto-switch policy to debug_log_only for noise reduction and flag diagnostics warning.

## Test Plan

### Browser UI tests
Create test file [orchestrator/web/static/app-chat-queue-steer.test.js](orchestrator/web/static/app-chat-queue-steer.test.js):
- submit behavior for queue and steer modes
- steer append coalescing
- queue multiple entries
- up arrow steer-now removal and dispatch
- stop visibility by state

Create test file [orchestrator/web/static/app-sandbox-task-strip.test.js](orchestrator/web/static/app-sandbox-task-strip.test.js):
- renders sandbox strip to right of timers/alarms
- running, success, error visual states
- click running task opens panel
- live log append updates panel without full rerender

Create test file [orchestrator/web/static/app-task-strip-subagents.test.js](orchestrator/web/static/app-task-strip-subagents.test.js):
- subagent chips render in timer-bar strip with correct state badges
- clicking subagent opens info panel and streams thinking in real time
- high-rate thinking append patches panel without full rerender

Create test file [orchestrator/web/static/app-task-strip-docker-exec.test.js](orchestrator/web/static/app-task-strip-docker-exec.test.js):
- docker exec chips display container and exec identifiers
- stdout/stderr log streams are ordered by sequence
- terminal row includes exit code and failure reason

Create test file [orchestrator/web/static/app-chat-diagnostics-settings.test.js](orchestrator/web/static/app-chat-diagnostics-settings.test.js):
- verbose/reasoning controls persist in UI state
- lifecycle policy selector updates routing behavior
- interim lifecycle rows render only when enabled
- debug log append path stores entries with bounded retention

### Python websocket routing tests
Create test file [orchestrator/web/test_realtime_service_chat_queue_steer.py](orchestrator/web/test_realtime_service_chat_queue_steer.py):
- chat_stop action routing and ack
- chat_steer_now routing and ack
- snapshot includes run state and queue items

Create test file [orchestrator/web/test_realtime_service_sandbox_tasks.py](orchestrator/web/test_realtime_service_sandbox_tasks.py):
- state snapshot includes sandbox task summaries
- sandbox_task_update broadcast format
- sandbox_task_log_append incremental behavior
- log retention and truncation metadata

Create test file [orchestrator/web/test_realtime_service_task_strip_subagents.py](orchestrator/web/test_realtime_service_task_strip_subagents.py):
- state snapshot includes subagent task summaries
- subagent_task_update and subagent_thinking_append contracts
- panel backfill action returns ordered thinking segments

Create test file [orchestrator/web/test_realtime_service_docker_exec_tasks.py](orchestrator/web/test_realtime_service_docker_exec_tasks.py):
- state snapshot includes docker exec task fields
- sandbox_exec_update and sandbox_exec_log_append contract validation
- sequence ordering and stream channel semantics

Create test file [orchestrator/web/test_realtime_service_chat_diagnostics.py](orchestrator/web/test_realtime_service_chat_diagnostics.py):
- chat_session_diagnostics_set routing and ack
- snapshot includes effective verbose/reasoning values
- chat_lifecycle_interim and chat_debug_log_append payload contracts

### Orchestrator flow tests
Create test file [test_web_ui_chat_stop_queue_steer_flow.py](test_web_ui_chat_stop_queue_steer_flow.py):
- stop cancels active pipeline and marks terminal state
- queue FIFO dispatch after completion
- steer-now supersedes queue and dispatches immediately
- stale response suppression after stop and steer

Create test file [test_web_ui_sandbox_task_visibility_flow.py](test_web_ui_sandbox_task_visibility_flow.py):
- sandbox task lifecycle to UI status transitions
- success output excerpt propagation
- error summary propagation
- live log streaming into detail panel path

Create test file [test_web_ui_task_strip_subagent_thinking_flow.py](test_web_ui_task_strip_subagent_thinking_flow.py):
- subagent run appears in timer bar while active
- clicking task opens info view and receives live thinking deltas
- terminal summary is shown and stream closes cleanly

Create test file [test_web_ui_docker_exec_task_flow.py](test_web_ui_docker_exec_task_flow.py):
- docker exec start/update/end emitted with container and exec ids
- stdout/stderr merged display preserves sequence order
- failure path includes exit code and error reason in panel

Create test file [test_web_ui_chat_lifecycle_diagnostics_flow.py](test_web_ui_chat_lifecycle_diagnostics_flow.py):
- new session applies selected verbose/reasoning before first upstream dispatch
- policy both emits interim chat and debug log entries
- policy debug_log_only suppresses interim chat rows
- OpenClaw Web UI sees updated session-level settings on newly created sessions

Create test file [test_web_ui_completion_detection_accuracy.py](test_web_ui_completion_detection_accuracy.py):
- strong-signal terminal classification wins over heuristic timeout path
- medium-signal corroboration requires two independent signals
- contradictory frames resolve by request_id and newest timestamp
- stale delayed frames do not flip active request completion state
- policy both path reaches >=92% classification accuracy against replay fixtures

Create test file [test_web_ui_transport_replay_and_reconcile.py](test_web_ui_transport_replay_and_reconcile.py):
- out-of-order stream_seq frames are dropped deterministically
- sequence gap triggers reconcile path and final snapshot classification
- reconnect + replay restores missing terminal event without duplicate UI terminal transitions

Create test file [test_web_ui_provider_lifecycle_adapter_contract.py](test_web_ui_provider_lifecycle_adapter_contract.py):
- weak-provider traces are normalized to internal lifecycle schema
- missing end with final synthesizes lifecycle_end when policy allows
- stream cut without final maps to terminal_timeout or terminal_error by adapter profile

Create test file [test_web_ui_stop_steer_generation_fencing.py](test_web_ui_stop_steer_generation_fencing.py):
- stop/steer increments generation and fences stale frames
- single terminal winner enforced per request_id+generation
- stale terminal entries are audit-logged with superseded_by reference

## Rollout Strategy
1. Add feature flags in config:
	- web_ui_chat_stop_enabled
	- web_ui_chat_queue_steer_enabled
	- web_ui_sandbox_task_strip_enabled
	- web_ui_sandbox_live_logs_enabled
	- web_ui_chat_diagnostics_controls_enabled
	- web_ui_lifecycle_interim_enabled
	- web_ui_lifecycle_debug_log_enabled
	- web_ui_task_strip_subagents_enabled
	- web_ui_task_strip_docker_exec_enabled
2. Gate UI controls by flags.
3. Enable in dev and verify telemetry.
4. Enable by default once stable.

## Risks and Mitigations
- Risk: race between stop ack and late stream updates.
  - Mitigation: enforce request_id match checks client and server side.
- Risk: duplicate dispatch when queue drains and steer-now overlaps.
  - Mitigation: atomic dequeue by queue item id and idempotent dispatch map.
- Risk: waiting spinner remains due to missed lifecycle end.
  - Mitigation: terminal fallback on explicit error, timeout, or final assistant segment.
- Risk: high-frequency sandbox logs cause UI jank.
	- Mitigation: throttled DOM patching, bounded buffers, incremental append, and panel-only heavy rendering.
- Risk: unbounded sandbox logs increase memory usage.
	- Mitigation: ring-buffer retention and truncation metadata in payloads.
- Risk: setting propagation races with first upstream dispatch on new session.
	- Mitigation: block first dispatch on patch acknowledgement with timeout fallback and explicit warning event.
- Risk: verbose and reasoning signals create excessive chat noise.
	- Mitigation: policy selector with debug-only mode and bounded interim row compaction.
- Risk: docker metadata may be missing in some sandbox executions.
	- Mitigation: synthetic identifiers with metadata-quality labels and reconciliation logs.
- Risk: real-time subagent thinking stream can overload UI on high-token bursts.
	- Mitigation: throttled patching, chunk coalescing, and panel-scoped rendering only.
- Risk: confidence targets are not met under provider-specific event sparsity.
	- Mitigation: maintain fallback quiet-window classifier and require replay-suite thresholds before default enablement.
- Risk: replay/reconcile overhead increases websocket and CPU usage.
	- Mitigation: bounded replay windows, selective reconcile only on sequence gap, and telemetry-based thresholds.
- Risk: generation fencing bugs can hide legitimate terminal events.
	- Mitigation: strict contract tests for request_id+generation semantics and debug-log audit trail for dropped frames.

## Acceptance Criteria
1. Stop button appears only during active in-flight run and disappears on terminal.
2. Queue mode allows multiple queued entries.
3. Steer mode appends additional queued steer text into the latest queued steer item.
4. Up arrow on queued item immediately steers current request and removes that item.
5. UI clearly shows active, failed, timed_out, and completed states.
6. Waiting indicators clear reliably on terminal states.
7. New user messages supersede stale in-flight responses.
8. Sandbox tasks are listed to the right of alarms/timers with clear running, success, and error states.
9. Clicking a running sandbox task opens detailed view with live logs.
10. Success output and error details are visible in both list summary and detail panel.
11. Voice orchestrator UI can set verbose and reasoning levels and persist them for the active chat context.
12. On new session startup, selected verbose/reasoning settings are applied upstream before first user dispatch when possible.
13. Lifecycle details are captured in debug logs and/or shown as interim chat messages according to policy.
14. OpenClaw Web UI reflects these new-session setting values because the orchestrator patches shared session settings.
15. Completion classification uses prioritized multi-signal logic with request_id-safe stale-frame suppression.
16. Completion detection accuracy in replay and contract suites reaches >=92% before default-on rollout.
17. Transport gaps and out-of-order frames are handled by sequence-aware replay/reconcile without double-final UI transitions.
18. Weak provider lifecycle semantics are normalized or synthesized to deterministic terminal categories.
19. Stop/steer supersession uses generation fencing so stale late terminal frames cannot reopen or overwrite active cycle state.
20. Timer-bar task strip includes both docker sandbox exec tasks and subagent runs with distinct origin badges.
21. Clicking a subagent task opens info view with real-time thinking stream and terminal summary.
22. Clicking a docker exec task opens info view with container/exec identifiers and ordered stdout/stderr logs.

