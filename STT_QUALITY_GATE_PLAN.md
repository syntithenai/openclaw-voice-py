# STT Ghost Transcript Suppression Plan

_Date: 2026-03-17_

## Goal

Reduce false Whisper transcripts that occur when the user did not actually speak, while preserving valid short spoken replies such as `yes`, `no`, `okay`, and `thanks` when they are clearly conversationally expected.

The system should stop relying on a growing phrase blacklist as the primary defense. Instead, it should make an explicit suppression decision using multiple signals from:

- transcript content
- timing and capture conditions
- conversation context
- upstream request/response context
- known self-echo and cut-in conditions

## Hard Requirement

If a transcript is suppressed as a ghost transcript, it must be treated as if it never became a user message.

That means a suppressed transcript must:

- not appear in the web UI
- not be appended to chat/session history
- not be sent to quick-answer
- not be sent upstream
- not be mirrored into any other session
- not trigger tools
- not affect wake/sleep conversation state except for internal diagnostic counters

Internal logging and metrics are allowed, but only as non-user-facing diagnostic events.

## Problem Statement

Current behavior already filters some low-value transcripts in `orchestrator/main.py`, including:

- blank or punctuation-only outputs
- `[inaudible]`-style normalization fallout
- a hardcoded ignore list for phrases like `thanks`, `hmm`, `sigh`
- self-echo overlap against recent TTS output
- a special cut-in filter for single-syllable words near cut-in start

This works as a first layer but has several limitations:

1. It is brittle.
  Every newly observed ghost phrase pushes the system toward a larger blacklist.

2. It is under-contextual.
  The same one-word transcript can be noise in one moment and a perfectly valid reply in another.

3. It is too lexical.
  Ghost transcripts are not just specific words. They are often artifacts of timing, self-echo, stale buffer audio, cut-in conditions, or low-signal noise.

4. The current pipeline appends the transcript to the web UI before quick-answer/upstream routing.
  That means any future suppression that happens too late would violate the requirement that ignored transcripts must not appear anywhere.

## Design Principles

1. Suppress early.
  The suppression decision must happen after STT normalization and before any UI/history/routing side effects.

2. Prefer rules over phrase accumulation.
  Keep a tiny explicit ignore list for only the most stable artifacts, but do not let it become the main strategy.

3. Make short-transcript handling context-sensitive.
  One-word replies are common and legitimate in conversation, so shortness alone cannot be a reject rule.

4. Use positive allow signals, not only reject signals.
  If the assistant has just asked a question, or the previous user request went upstream and the response ended with a question, a short answer should be easier to accept.

5. Keep the first implementation deterministic.
  No model-based classifier is required. A rule engine with weighted signals is enough.

6. Enforce immediately when implemented.
  Do not start in shadow mode. Ship with conservative thresholds, diagnostics, and a kill switch.

## Proposed Pipeline Placement

### Current effective order

Today the relevant order is approximately:

1. capture audio chunk
2. transcribe with Whisper
3. normalize transcript
4. apply a few hardcoded low-signal filters
5. append transcript to `pending_transcripts`
6. debounce and combine transcript(s)
7. increment request id
8. append user message to web UI
9. run quick-answer or upstream flow

### Proposed order

Insert a dedicated `ghost transcript suppression gate` after normalization and before any queuing or UI emission:

1. capture audio chunk
2. transcribe with Whisper
3. normalize transcript
4. run existing hard rejects:
  - blank
  - punctuation-only
  - explicit unusable markers
5. compute suppression context snapshot
6. run ghost transcript suppression gate
7. if `suppress`: record internal diagnostic event and return immediately
8. if `accept`: continue to pending transcript queue / debounce path
9. only after final accepted transcript assembly should it become a user message for UI and routing

This is the critical architectural requirement. If the gate sits later than this, suppressed transcripts may leak into the web UI or downstream state.

## Decision Model

Use a deterministic rule engine with three conceptual outcomes:

- `ACCEPT`
- `SUPPRESS_GHOST`
- `REVIEWABLE_ACCEPT`

`REVIEWABLE_ACCEPT` is still accepted behaviorally. It only means `accepted, but log why this was borderline`.

The implementation does not need to expose three branches publicly. Internally it can simply return:

- `accepted: bool`
- `reason_codes: list[str]`
- `score: float`
- `context_flags: dict[str, bool]`

## Signals

### A. Transcript-form signals

These are derived from the normalized transcript itself.

- token count
- character count
- whether transcript is a single word
- whether transcript is two words or fewer
- whether transcript matches known ghost phrases
- whether transcript is mostly punctuation or non-lexical markers
- whether transcript has low lexical diversity
- whether transcript is an exact or near-exact match to recent TTS text
- whether transcript is an acknowledgment token set such as `yes`, `no`, `okay`, `sure`, `thanks`, `right`, `yep`, `nope`

### B. Acoustic / STT-quality proxy signals

Use these only if available from the Whisper runtime or adjacent capture pipeline.

- no-speech proxy
- avg logprob proxy
- compression ratio proxy
- speech duration estimate
- cut-in elapsed time from TTS start or TTS interruption
- recent ring-buffer clear / stale-audio indicators

If some fields are unavailable, the rule engine should degrade gracefully and ignore them.

### C. Playback and echo-risk signals

- `tts_playing`
- recent TTS playback within N milliseconds
- overlap with recent/current TTS text
- transcript occurs during cut-in window
- transcript occurs immediately after stop/interrupt of TTS

### D. Conversation-context signals

- previous assistant turn existed
- previous assistant turn ended with a question mark
- previous assistant turn was semantically a question even if punctuation is absent
- previous assistant turn asked for confirmation or a short reply
- previous accepted user turn was recent
- current transcript is plausibly a direct answer to previous assistant turn

### E. Upstream-context signals

These are important and should be stronger than generic conversation context.

- previous accepted user request was sent upstream
- previous accepted user request was handled locally only
- previous upstream assistant response ended in a question
- previous upstream assistant response asked for confirmation/clarification
- previous upstream request is still the active conversational thread

The intent here is:

- if the system just asked the user something after an upstream turn, then a short follow-up like `yes`, `no`, `tomorrow`, `home`, `both`, or `google` is often legitimate and should not be suppressed

## Context Snapshot Contract

Before evaluating a transcript, compute a small context object such as:

```text
GhostContext
- transcript_text
- canonical_transcript
- token_count
- char_count
- is_single_word
- is_short_transcript
- tts_playing
- ms_since_tts_end
- ms_since_last_assistant_turn
- last_assistant_turn_text
- last_assistant_turn_was_question
- last_assistant_turn_requested_short_reply
- last_user_turn_went_upstream
- last_upstream_response_was_question
- last_upstream_response_requested_confirmation
- cut_in_active
- ms_from_cut_in_start
- self_echo_similarity
- whisper_quality_signals_available
- whisper_quality_summary
```

This structure keeps the decision logic explicit and testable instead of scattering ad hoc checks throughout the orchestration path.

## Rule Priority

Use ordered priority rather than a single opaque score.

1. Hard reject rules
2. Hard allow rules
3. Strong suppress rules
4. Strong allow rules
5. Weighted tie-breakers
6. Default action

Default action for the first implementation should be:

- accept normal-length transcripts
- suppress only when strong evidence indicates ghost/self-echo/no-speech artifact

This keeps the system from becoming too aggressive.

## Concrete Rule Table

The table below is the proposed first implementation behavior.

| Priority | Rule | Condition | Action | Notes |
|---|---|---|---|---|
| 1 | Empty transcript reject | Transcript is empty after normalization | `SUPPRESS_GHOST` | Already effectively exists |
| 1 | Punctuation-only reject | No alphanumeric content | `SUPPRESS_GHOST` | Already effectively exists |
| 1 | Explicit unusable marker reject | Transcript is known unusable marker like `[inaudible]` after normalization | `SUPPRESS_GHOST` | Stable hard reject |
| 1 | Exact self-echo reject | Transcript exactly matches or strongly overlaps recent/current TTS output above strict threshold | `SUPPRESS_GHOST` | Strongest non-empty reject |
| 1 | Active playback echo reject | `tts_playing=true` and transcript matches known self-echo artifact during playback | `SUPPRESS_GHOST` | Covers phrases like `you're welcome` |
| 2 | Question-answer allow | Transcript has <= 3 tokens and previous assistant turn was a question | `ACCEPT` | Main protection for short valid replies |
| 2 | Upstream-question allow | Transcript has <= 3 tokens and previous accepted request went upstream and latest upstream response was a question | `ACCEPT` | Stronger than generic question signal |
| 2 | Upstream-clarification allow | Transcript has <= 4 tokens and latest upstream response requested confirmation/disambiguation | `ACCEPT` | Handles replies like `the blue one` |
| 2 | Direct confirmation allow | Transcript is from small confirmation set and assistant just asked yes/no style question | `ACCEPT` | `yes`, `no`, `yep`, `nope`, `correct` |
| 3 | Ghost-phrase strong suppress | Transcript is in explicit ghost artifact list and no allow rule matched | `SUPPRESS_GHOST` | Keep list intentionally small |
| 3 | Cut-in blip suppress | Single short word captured within early cut-in window and not covered by question/upstream allow | `SUPPRESS_GHOST` | Generalizes current single-syllable cut-in filter |
| 3 | Playback-tail blip suppress | Single-word transcript occurs very shortly after TTS end and self-echo risk is high | `SUPPRESS_GHOST` | Protects playback tails |
| 3 | Low-signal acknowledgment suppress | Transcript is a known acknowledgment token and previous assistant turn was not a question and no upstream-question context exists | `SUPPRESS_GHOST` | This is the key `hello/thanks/okay` ghost rule |
| 3 | Standalone greeting suppress | Greeting-only transcript occurs with no conversational prompt and no fresh wake/intent evidence | `SUPPRESS_GHOST` | Covers `hello`, `hi` ghost captures |
| 4 | Upstream-follow-up bias allow | Previous accepted request went upstream, transcript is short, and assistant response was recent even if not punctuated as question | `ACCEPT` | Handles clarification turns with imperfect punctuation |
| 4 | Recent assistant prompt allow | Previous assistant turn was recent and contains prompt verbs like `which`, `when`, `where`, `confirm`, `want`, `choose` | `ACCEPT` | Semantic question without `?` |
| 5 | Weighted borderline decision | Use weighted score from lexical + timing + echo-risk + upstream context | `ACCEPT` or `SUPPRESS_GHOST` | Only for unresolved cases |
| 6 | Normal transcript default | Transcript has >= 4 tokens and no strong suppress flags | `ACCEPT` | Avoid overfitting to short transcript problem |

## Recommended Short-Transcript Policy

### Definition

For the first implementation:

- `single-word transcript`: exactly 1 token
- `short transcript`: 1 to 3 tokens
- `borderline short transcript`: 4 tokens with low information density

### Default posture

Short transcripts should be considered suspicious only when they lack conversational support.

That means:

- `yes` after `Do you want me to continue?` should pass
- `home` after upstream asked `Which location?` should pass
- `okay` immediately after a declarative bot answer with no question should usually be suppressed if it looks like a ghost capture
- `hello` with no conversational prompt should usually be suppressed

### Why this is not "too much"

This is not over-engineering if it is kept rule-based and narrow in scope.

The system is not trying to infer full semantics. It is only answering a small question:

`Is this short transcript plausibly a real user reply to what just happened?`

That is a reasonable use of conversation context and should materially outperform a growing ignore list.

## Allow-Signal Matrix For Short Transcripts

Use the following precedence for short transcripts.

| Signal | Strength | Effect |
|---|---|---|
| Previous upstream response explicitly asked a question | Very strong allow | Do not suppress solely for being short |
| Previous assistant turn asked a question | Strong allow | Do not suppress solely for being short |
| Previous upstream response requested confirmation/disambiguation | Very strong allow | Allow even 1-word reply |
| Previous accepted user request went upstream | Medium allow | Reduce suppression bias |
| Previous assistant turn was recent and prompt-like | Medium allow | Reduce suppression bias |
| TTS self-echo overlap is high | Very strong suppress | Usually override allow unless direct answer is extremely clear |
| Transcript is on explicit ghost list | Strong suppress | Override only with explicit question/clarification context |
| Transcript is one word and previous assistant turn was not a question | Strong suppress | Unless upstream or prompt allow exists |

## Weighted Tie-Breaker Model

After priority rules run, unresolved cases can use a simple additive score.

Start at `0`.

### Add points

- `+4` previous upstream response was a question
- `+3` previous assistant turn was a question
- `+3` previous upstream response requested clarification/confirmation
- `+2` previous accepted request went upstream
- `+2` transcript is 2-3 words and semantically looks like a slot value reply
- `+1` transcript length >= 4 words

### Subtract points

- `-5` high-confidence self-echo overlap with recent/current TTS
- `-4` explicit ghost artifact phrase
- `-3` single-word acknowledgment with no prior question context
- `-3` transcript captured inside early cut-in blip window
- `-2` transcript occurred immediately after TTS tail with elevated echo risk
- `-2` low-quality whisper/no-speech indicators if available

### Decision threshold

- score `>= 1`: `ACCEPT`
- score `<= 0`: `SUPPRESS_GHOST`

This should be easy to reason about and easy to tune.

## Explicit Ghost Artifact List

Keep an explicit list, but narrow it to stable, repeated artifacts.

Examples:

- `hello`
- `hi`
- `thanks`
- `thank you`
- `hmm`
- `sigh`
- `you're welcome` during playback-tail conditions

Rules for this list:

1. Entries should represent recurring artifacts, not ordinary language in general.
2. List membership alone should not suppress a transcript if a strong allow rule is active.
3. The list should stay small and curated.
4. Every added phrase should have a logged example or reproducible scenario.

## Upstream-Aware Conversation Rules

This is the most important addition beyond the current ignore-list approach.

### Rule family 1: previous request went upstream

If the last accepted user turn was sent upstream, then the next short transcript should be treated as more likely legitimate.

Reason:
Upstream turns are more likely to produce follow-up questions, clarifications, choices, or confirmations than quick local command handling.

### Rule family 2: upstream response ended in a question

If the last upstream assistant response ended in a question or was classified as a clarification request, the next short transcript should usually be accepted unless strong self-echo evidence exists.

Examples:

- Bot: `Which playlist do you want?`
  User: `jazz`
  Result: accept

- Bot: `Do you want me to continue?`
  User: `yes`
  Result: accept

- Bot: `Should I use the browser or the terminal?`
  User: `browser`
  Result: accept

### Rule family 3: upstream sent but response was declarative

If the previous request went upstream but the latest upstream response was declarative and not a question, the next single-word acknowledgment should still be considered suspicious.

Examples:

- Bot: `The build completed successfully.`
  Ghost transcript: `okay`
  Result: usually suppress

This prevents the upstream signal from becoming too permissive.

## Assistant-Question Detection

Do not depend only on trailing `?`.

Use a helper classification such as `assistant_turn_expects_short_reply` based on:

- trailing question mark
- starts with or contains prompt words such as:
  - `which`
  - `what`
  - `when`
  - `where`
  - `who`
  - `do you want`
  - `would you like`
  - `should I`
  - `can you confirm`
  - `which one`
  - `choose`
  - `pick one`
- clarification patterns like:
  - `did you mean`
  - `do you mean`
  - `which one do you mean`

This helps catch prompt-like turns that lack strict punctuation.

## Data Flow Requirements

The suppression decision needs access to recent accepted conversation state.

Maintain small in-memory state such as:

- last accepted user transcript text
- whether last accepted user transcript was sent upstream
- last assistant text
- whether last assistant text was quick-answer or upstream
- whether last assistant turn was a question
- whether last upstream assistant turn was a question
- timestamps for last assistant completion and last TTS completion

This state should be updated only for accepted turns.

Suppressed transcripts must not mutate this conversation summary.

## Web UI Requirement

The web UI must only ever receive accepted transcripts.

That implies:

- do not call `web_service.append_chat_message({... role: "user" ...})` until after suppression has passed
- do not emit partial or final UI events for suppressed transcripts
- do not mirror suppressed user turns to the OpenClaw session

If debugging needs a UI view of suppressed events, it should be in a separate diagnostics pane or hidden debug endpoint, never in the normal chat timeline.

## Logging and Metrics

The first implementation should log enough information to tune behavior without exposing suppressed transcripts to the user-facing UI.

### Per suppressed event log

Log:

- normalized transcript
- canonical transcript
- decision: `SUPPRESS_GHOST`
- matched rule ids
- allow signals considered
- suppress signals considered
- whether previous request went upstream
- whether previous assistant turn was a question
- whether previous upstream response was a question
- self-echo similarity score if available
- timing values like `ms_since_tts_end`, `ms_since_last_assistant_turn`, `ms_from_cut_in_start`

### Counters

- suppressed ghost transcripts total
- suppressed single-word transcripts total
- suppressed by self-echo total
- suppressed by no-question short-reply rule total
- accepted short transcripts after assistant question total
- accepted short transcripts after upstream question total

## Configuration Surface

Add explicit config knobs so the behavior is adjustable without code changes.

Suggested settings:

- `ghost_filter_enabled=true`
- `ghost_filter_single_word_enabled=true`
- `ghost_filter_require_question_for_acks=true`
- `ghost_filter_playback_tail_ms=1200`
- `ghost_filter_cutin_early_ms=500`
- `ghost_filter_recent_assistant_ms=12000`
- `ghost_filter_upstream_context_ms=20000`
- `ghost_filter_self_echo_similarity_threshold=0.75`
- `ghost_filter_debug_logging=true`
- `ghost_filter_kill_switch=false`

The kill switch is important because this feature is intended to enforce immediately when turned on.

## Rollout Plan

Do not use shadow mode.

### Phase 1

Implement deterministic suppression gate with:

- early pipeline placement
- short transcript context rules
- upstream-question allow rules
- minimal curated ghost phrase list
- diagnostics and kill switch

### Phase 2

Tune thresholds from real usage logs.

Focus tuning on:

- false suppression of valid short answers
- missed ghost acknowledgments after declarative bot statements
- playback-tail self-echo cases

### Phase 3

Optionally enrich with additional Whisper-quality proxies if the runtime makes them reliably available.

## Test Matrix

### Unit tests: hard reject and accept rules

1. Blank transcript is suppressed.
2. Punctuation-only transcript is suppressed.
3. Exact self-echo transcript during playback is suppressed.
4. Single-word transcript after assistant question is accepted.
5. Single-word transcript after upstream question is accepted.
6. Single-word acknowledgment after declarative assistant turn is suppressed.
7. Greeting-only ghost transcript after declarative assistant turn is suppressed.
8. Explicit ghost phrase after upstream question is accepted only if allow rule is stronger and no self-echo match exists.

### Unit tests: upstream-aware scenarios

1. Previous user turn went upstream, latest upstream response asks `Which one?`, user says `browser` -> accept.
2. Previous user turn went upstream, latest upstream response says `Done.` with no question, next transcript `okay` -> suppress.
3. Previous user turn handled locally, assistant says `Timer started.`, next transcript `thanks` -> suppress.
4. Previous upstream response asks `Tomorrow or Friday?`, next transcript `tomorrow` -> accept.

### Unit tests: cut-in and playback-tail scenarios

1. Single short blip within early cut-in window with no question context -> suppress.
2. `yes` in early cut-in window after assistant asks `Should I continue?` -> accept.
3. Playback-tail transcript highly similar to recent TTS -> suppress.

### Integration tests

1. Suppressed transcript does not appear in pending transcript queue.
2. Suppressed transcript does not increment request id.
3. Suppressed transcript does not appear in web UI.
4. Suppressed transcript does not reach quick-answer.
5. Suppressed transcript does not reach upstream gateway.
6. Suppressed transcript does not update mirrored session history.

## Suggested Implementation Shape

### Helper functions

- `build_ghost_context(...) -> GhostContext`
- `classify_assistant_turn_expectation(text) -> AssistantExpectation`
- `detect_short_transcript_kind(text) -> ShortTranscriptKind`
- `score_self_echo_similarity(transcript, recent_tts_texts) -> float`
- `decide_ghost_transcript(ctx) -> GhostDecision`

### Decision object

```text
GhostDecision
- accepted: bool
- reason_codes: list[str]
- score: int | float
- matched_priority_rule: str
```

### Suggested insertion point in `orchestrator/main.py`

Immediately after:

- transcript normalization
- existing blank / punctuation normalization filtering

and before:

- emotion tagging side effects if those should not run for suppressed transcripts
- `pending_transcripts.append(...)`
- debounce scheduling
- user-message UI append

If emotion detection remains before suppression, document why. Otherwise, suppression should ideally happen before expensive downstream work as well.

## Open Questions

1. Should suppressed transcripts increment any hidden turn counter for debugging, or should they be completely invisible except logs/metrics?
2. Do we want a separate `ghost_filter_artifacts.yaml` or similar config file for the curated phrase set instead of hardcoding it?
3. Can the current Whisper client expose additional confidence proxies cheaply, or is the first version better kept purely rule-and-context based?
4. Should the web UI eventually expose a developer-only diagnostics view for suppressed transcripts, disabled by default?

## Recommended First-Version Summary

Implement the smallest version that materially improves behavior:

1. Add an early suppression gate before queue/UI/routing.
2. Keep hard rejects for blank and punctuation-only transcripts.
3. Keep self-echo suppression.
4. Replace the growing blacklist mindset with short-transcript context rules.
5. Accept short replies when the assistant just asked a question.
6. Accept short replies even more readily when the previous request went upstream and the upstream response asked a question.
7. Suppress greeting/acknowledgment one-word transcripts when the previous assistant turn was not a question.
8. Log every suppression reason for tuning.
9. Ship it directly with a kill switch, not shadow mode.

That should give a much better balance than continuing to add isolated words to an ignore list.

## Implementation Checklist Mapped To Current Code

This checklist maps the plan to concrete touchpoints so implementation can be executed directly.

### A) Add config flags

1. Add new `VoiceConfig` fields in `orchestrator/config.py`:
  - `ghost_filter_enabled`
  - `ghost_filter_single_word_enabled`
  - `ghost_filter_require_question_for_acks`
  - `ghost_filter_playback_tail_ms`
  - `ghost_filter_cutin_early_ms`
  - `ghost_filter_recent_assistant_ms`
  - `ghost_filter_upstream_context_ms`
  - `ghost_filter_self_echo_similarity_threshold`
  - `ghost_filter_debug_logging`
  - `ghost_filter_kill_switch`
2. Keep defaults conservative and compatible with current behavior when disabled.
3. Document env variable names in `.env.example` and relevant ops docs.

### B) Add conversation-state tracking in orchestrator runtime scope

In `orchestrator/main.py` near current runtime state declarations (`current_request_id`, `last_gateway_send_ts`, `last_tts_ts`, etc.), add state needed by ghost-filter context:

1. Last accepted user turn metadata:
  - `last_user_text`
  - `last_user_accepted_ts`
  - `last_user_went_upstream`
2. Last assistant turn metadata:
  - `last_assistant_text`
  - `last_assistant_source` (`quick_answer` or `gateway`)
  - `last_assistant_ts`
  - `last_assistant_was_question`
  - `last_assistant_expects_short_reply`
3. Last upstream assistant metadata:
  - `last_upstream_assistant_text`
  - `last_upstream_assistant_ts`
  - `last_upstream_response_was_question`
  - `last_upstream_response_requested_confirmation`
4. Suppression counters for diagnostics:
  - total suppressed
  - suppressed by short-no-question rule
  - suppressed by self-echo rule
  - accepted short-after-question
  - accepted short-after-upstream-question

### C) Add helper functions (same module first pass)

In `orchestrator/main.py`, add deterministic helpers close to existing transcript utilities (`canonicalize_transcript_for_match`, `normalize_transcript`, `is_likely_tts_self_echo`):

1. `is_ack_token(canonical: str) -> bool`
2. `is_greeting_token(canonical: str) -> bool`
3. `assistant_turn_expects_short_reply(text: str) -> bool`
4. `assistant_turn_is_question(text: str) -> bool`
5. `build_ghost_context(...) -> GhostContext-like dict/dataclass`
6. `decide_ghost_transcript(ctx) -> GhostDecision`
7. Optional: `log_ghost_decision(ctx, decision)` for compact structured diagnostics

Keep this as pure logic with no side effects, so unit tests are simple.

### D) Insert suppression gate at the STT entry point

Primary insertion point: `process_chunk(...)` in `orchestrator/main.py`.

Current sequence in this function is:

1. `whisper_client.transcribe(...)`
2. `normalize_transcript(...)`
3. hardcoded low-signal phrase suppression
4. `is_likely_tts_self_echo(...)`
5. cut-in single-syllable filter
6. optional emotion detection
7. `pending_transcripts.append(...)`

Required changes:

1. Keep blank/punctuation normalization guard.
2. Build context snapshot.
3. Run `decide_ghost_transcript`.
4. If suppressed, log/counter and `return` immediately.
5. Only then continue to emotion detection and queueing.

This ensures suppressed transcripts never enter `pending_transcripts`.

### E) Remove/reshape brittle ignore-list block

In `process_chunk(...)`, replace the hardcoded `canonical_transcript in {...}` phrase block with:

1. a much smaller curated artifact set, and
2. integration into `decide_ghost_transcript` so strong allow-context can override where appropriate.

Do not keep a long unconditional phrase blacklist in-line.

### F) Preserve and parameterize self-echo behavior

`is_likely_tts_self_echo(...)` already exists and should remain a strong signal.

Implementation details:

1. Keep exact-match and high-overlap checks.
2. Move thresholds/window values to new ghost-filter config where practical.
3. Feed resulting similarity/risk flags into `decide_ghost_transcript`.

### G) Ensure user message creation only for accepted transcripts

In `send_debounced_transcripts(...)`, user-message side effects currently happen at:

- request id increment
- console `→ USER`
- `web_service.append_chat_message({... role: "user" ...})`

Because suppression now happens earlier in `process_chunk(...)`, this path should only receive accepted transcripts.

Validation checks:

1. No suppressed transcript reaches request-id increment.
2. No suppressed transcript appears in web UI timeline.
3. No suppressed transcript enters quick-answer/upstream routing.

### H) Track upstream signals when routing completes

Update state in `send_debounced_transcripts(...)` once routing decision is known:

1. On quick-answer local handling (`should_use_upstream == False`):
  - mark last accepted user turn as `went_upstream=False`
  - store assistant quick-answer text metadata
  - compute question/short-reply expectation for that assistant text
2. On upstream send path (`should_send_to_gateway == True` and response received):
  - mark last accepted user turn as `went_upstream=True`
  - store upstream assistant response metadata
  - compute `last_upstream_response_was_question`
  - compute `last_upstream_response_requested_confirmation`

This step is necessary for the next transcript’s short-reply allow logic.

### I) Decide scope for web UI typed chat

`_ui_chat_text(...)` currently normalizes and appends directly to `pending_transcripts`.

Recommended first version:

1. Do **not** apply ghost suppression to typed text.
2. Keep ghost suppression limited to STT-originated transcripts from `process_chunk(...)`.
3. If needed later, add a source flag to avoid accidental filtering of typed messages.

### J) Add tests (new focused unit tests)

Create unit tests for decision logic in a new test module (for example under `openclaw-voice/test/`):

1. short transcript after non-question assistant turn -> suppress
2. short transcript after assistant question -> accept
3. short transcript after upstream-question response -> accept
4. short transcript after upstream declarative response -> suppress when ack/greeting-like
5. high self-echo overlap -> suppress
6. empty/punctuation-only -> suppress
7. normal multi-word transcript with no suppress signals -> accept

Add at least one integration-like test for `process_chunk` path to verify suppressed transcripts never reach queueing/UI side effects.

### K) Add operational logging and counters

At suppression decision points:

1. Log compact reason codes and key context flags.
2. Increment reason-specific counters.
3. Keep logs non-user-facing and concise.

This is required for threshold tuning once enabled.

### L) Rollout execution order (no shadow mode)

1. Add config + helper functions.
2. Wire early suppression gate in `process_chunk`.
3. Wire upstream/assistant context tracking in `send_debounced_transcripts`.
4. Add tests for rule engine and suppression placement.
5. Enable in active mode with conservative defaults.
6. Tune from real logs using reason counters.

## Definition Of Done

Implementation is complete when all are true:

1. Suppressed STT transcripts are never queued, never shown in web UI, and never routed upstream.
2. One-word valid replies after assistant/upstream questions are accepted reliably.
3. One-word acknowledgments after non-question assistant turns are suppressed in common ghost scenarios.
4. Existing self-echo protections remain effective or stronger.
5. Decision logs/counters clearly explain suppression outcomes.
6. Tests cover the new rules and pass.
