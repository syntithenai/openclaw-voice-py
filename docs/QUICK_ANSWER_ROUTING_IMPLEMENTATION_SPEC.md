# Quick Answer Routing Implementation Spec

Date: 2026-03-29
Status: planning only
Scope: voice orchestrator quick-answer routing, built-in skill detection, model-tier recommendation, upstream model selection

## Goals

1. Strengthen deterministic routing for built-in voice skills so more requests are handled without any LLM call.
2. Ensure the quick-answer LLM fallback only returns one of two valid outcomes:
   - built-in tool calls for supported local skills
   - a model-tier recommendation for upstream escalation
3. Support the recorder skill explicitly in both deterministic routing and quick-answer tool fallback.
4. Map model-tier recommendations to configured OpenClaw model IDs and prepend an inline `/model` directive only when a valid configured match exists.

## Explicitly Supported Built-In Skills

The procedural local-routing layer must explicitly cover these built-in skills:

1. timers
2. alarms
3. music
4. recorder
5. new session

Recorder is in scope for the new contract and must be treated as a first-class built-in skill, not a special case.

## Date and Time Quick-Answer Pathway

Simple current date and time questions — including basic date arithmetic — must be handled locally by the quick-answer LLM without upstream escalation.

The quick-answer LLM already receives the current timestamp injected into the system prompt as `current_datetime`. It can answer these questions entirely from that value.

### Pattern allowlist (checked before time-sensitive escalation)

Queries that match any of these patterns must proceed to quick-answer rather than escalate upstream:

1. `what time is it` / `what is the time`
2. `what day is it` / `what is the day`
3. `what is today's date` / `what's the date` / `what is the date today`
4. `what day of the week` / `what day of the year`
5. `what month is it` / `what year is it`
6. `how many days until` / `how many days since` (simple arithmetic)
7. `what day is tomorrow` / `what day was yesterday`
8. `what week is it` / `what week number`
9. `how long until` / `how long ago` (when no web lookup is implied)

These patterns MUST be checked before the generic `TIME_SENSITIVE_PATTERNS` check so that words like `today` and `now` embedded in date questions do not trigger upstream escalation.

### Scope boundaries

In scope (quick-answer local):

- Current time, date, day of week, month, year
- Simple date arithmetic: days until the weekend, days since last Monday, what day is 10 days from now
- What week number it is

Out of scope (upstream):

- Calendar event lookup or scheduling
- Countdown to named public events (e.g. "how many days until Christmas" requires knowing the holiday date without context)
- Historical date lookups (e.g. "when was World War II")

## Non-Goals

1. No free-form quick-answer speech responses from the quick-answer LLM.
2. No new generic tool families beyond the built-in voice skills above.
3. No upstream protocol changes are required if inline `/model` prefixing is used.

## Required Routing Stages

### Stage 1: deterministic built-in skill routing

The voice orchestrator must first run a deterministic skill matcher before any quick-answer LLM call.

Possible outputs:

1. `LOCAL_SKILL_MATCH`
2. `NO_LOCAL_SKILL_MATCH`

If a local skill matches, the orchestrator executes the corresponding built-in handler immediately and uses that skill's existing TTS-suitable response.

### Stage 2: quick-answer LLM classification fallback

If Stage 1 fails to match a built-in skill, the quick-answer LLM is called with a strict response contract.

Allowed outputs:

1. built-in tool calls only
2. model-tier recommendation only

Disallowed outputs:

1. plain prose content
2. `USE_UPSTREAM_AGENT`
3. unsupported tool names
4. mixed responses containing both tool calls and recommendation content
5. partial or ambiguous recommendation text

### Stage 3: upstream gateway send

If Stage 2 returns a model recommendation, the orchestrator resolves it to a configured model ID.

If a valid model ID is found, the upstream message must be rewritten as:

```text
/model <provider/model-id> <original transcript>
```

Example:

```text
/model lmstudio/oss-120b what is the tallest building in the world
```

If no configured model match exists for the recommended tier or any less powerful tier, no `/model` directive is sent and the original transcript is sent unchanged.

## Deterministic Skill Matcher Specification

### Proposed component

Add a dedicated deterministic matcher layer in the voice repo.

Suggested internal types:

```python
class BuiltInSkillMatch(TypedDict):
    skill: Literal["timers", "alarms", "music", "recorder", "new_session"]
    confidence: Literal["strong", "medium"]
    reason: str


class BuiltInSkillSpec(TypedDict):
    skill: Literal["timers", "alarms", "music", "recorder", "new_session"]
    enabled: bool
    strong_patterns: list[str]
    medium_patterns: list[str]
    negative_patterns: list[str]
```
```

### Matching rules

1. Normalize transcript first.
2. Check strong patterns in priority order.
3. Apply negative guards to prevent false positives.
4. If multiple skills match, resolve by explicit precedence.

### Transcript normalization requirements

1. lowercase
2. trim whitespace
3. collapse repeated whitespace
4. strip trailing punctuation
5. normalize contractions where useful
6. normalize number words where existing parsers can benefit
7. drop filler prefixes like `please`, `could you`, `can you`, `would you`, `hey`, `okay`

### Skill precedence

When multiple built-in skills match, use this precedence order:

1. new session
2. recorder
3. alarms
4. timers
5. music

This precedence is intended to keep explicit session and recorder commands from being swallowed by generic music or timer keywords.

### Minimum vocabulary expansion

The deterministic matcher must widen vocabulary at least as follows.

#### Timers

Strong phrases:

1. `set a timer for`
2. `start a timer for`
3. `start a countdown for`
4. `count down for`
5. `timer for`
6. `remind me in`

Medium phrases:

1. `countdown`
2. `time for`
3. `time x minutes`

#### Alarms

Strong phrases:

1. `set an alarm for`
2. `wake me at`
3. `wake me up at`
4. `alarm for`
5. `ring at`
6. `get me up at`

Medium phrases:

1. `morning alarm`
2. `bedtime alarm`
3. `cancel alarm`
4. `stop alarm`

#### Music

Strong phrases:

1. `play music`
2. `play some`
3. `put on`
4. `queue up`
5. `pause music`
6. `resume music`
7. `stop music`
8. `skip this`
9. `next song`
10. `previous song`

Medium phrases:

1. `play`
2. `pause`
3. `resume`
4. `queue`
5. `playlist`

Music negative guards should prevent obvious timer, alarm, recorder, and new-session phrases from being claimed by the music matcher.

#### Recorder

The recorder skill must match exactly and only these two phrases (case-insensitive, filler-stripped):

1. `start recording`
2. `stop recording`

No other phrasing triggers the recorder skill. Variations such as `begin recording`, `end recording`, `turn recording on`, etc. must NOT be matched — they should fall through to the quick-answer LLM or upstream.

Recorder must only be treated as local when the recorder skill is actually enabled. If recorder is disabled, neither phrase must be marked local.

#### New session

The new session skill must match only commands that contain the words `start new` followed by (or immediately preceding) either `chat` or `session`.

Strong phrases (exact intent, filler-stripped):

1. `start new session`
2. `start new chat`
3. `start a new session`
4. `start a new chat`

No other phrasing triggers the new session skill. Phrases like `fresh chat`, `new conversation`, `start over`, `reset this conversation`, etc. must NOT be matched.

## Quick-Answer LLM Contract

### Required model behavior

The quick-answer LLM prompt must be updated so it is required to do exactly one of the following:

1. return valid built-in tool calls for timers, alarms, music, recorder, or new session
2. return a structured model-tier recommendation for upstream handling

It must never return plain text speech content.

### Allowed model tiers

The only valid recommendation tiers are:

1. `FAST`
2. `BASIC`
3. `CAPABLE`
4. `SMART`
5. `GENIUS`

Ascending power order is exactly:

```text
FAST < BASIC < CAPABLE < SMART < GENIUS
```

### Exact quick-answer recommendation payload shape

The recommendation response must be accepted only in this JSON form in assistant message content:

```json
{
  "type": "model_recommendation",
  "recommendation": "SMART",
  "reason": "current events or factual lookup likely needs a stronger upstream model"
}
```

#### Field requirements

1. `type`
   - required
   - must equal `model_recommendation`
2. `recommendation`
   - required
   - must be one of `FAST`, `BASIC`, `CAPABLE`, `SMART`, `GENIUS`
3. `reason`
   - optional but recommended
   - plain string for logs only
   - must not affect routing behavior

### Exact parsed internal representation

Suggested internal type:

```python
class ModelRecommendation(TypedDict):
    type: Literal["model_recommendation"]
    recommendation: Literal["FAST", "BASIC", "CAPABLE", "SMART", "GENIUS"]
    reason: str
```
```

### Tool-call contract

The quick-answer LLM may also return tool calls using the existing OpenAI-compatible `tool_calls` field. Only built-in skill tools are valid.

Allowed tool families:

1. timer and alarm tools
2. music tools
3. recorder tool
4. new-session tool

Recorder must remain present in `tool_definitions` when recorder support is enabled.

### Invalid quick-answer outputs

The parser must reject and escalate upstream on any of the following:

1. assistant `content` containing plain prose
2. malformed JSON recommendation
3. unknown `type`
4. invalid recommendation value
5. both `tool_calls` and recommendation JSON in the same response
6. unsupported tool name
7. empty response

## Exact Config Variable Names

Add these config variables to the voice orchestrator configuration.

### Existing variables retained

1. `QUICK_ANSWER_ENABLED`
2. `QUICK_ANSWER_LLM_URL`
3. `QUICK_ANSWER_MODEL`
4. `QUICK_ANSWER_API_KEY`
5. `QUICK_ANSWER_TIMEOUT_MS`
6. `QUICK_ANSWER_MIRROR_ENABLED`
7. `QUICK_ANSWER_BYPASS_WINDOW_MS`

### New feature flags

1. `QUICK_ANSWER_STRICT_ROUTING_ENABLED`
   - type: boolean
   - default: `true`
   - meaning: enable strict two-outcome quick-answer contract

2. `QUICK_ANSWER_PROCEDURAL_SKILL_ROUTING_ENABLED`
   - type: boolean
   - default: `true`
   - meaning: enable widened deterministic built-in skill routing before quick-answer LLM

### New model-tier mapping variables

1. `QUICK_ANSWER_MODEL_TIER_FAST_ID`
2. `QUICK_ANSWER_MODEL_TIER_BASIC_ID`
3. `QUICK_ANSWER_MODEL_TIER_CAPABLE_ID`
4. `QUICK_ANSWER_MODEL_TIER_SMART_ID`
5. `QUICK_ANSWER_MODEL_TIER_GENIUS_ID`

All five are optional strings.

### Example environment block

```env
QUICK_ANSWER_STRICT_ROUTING_ENABLED=true
QUICK_ANSWER_PROCEDURAL_SKILL_ROUTING_ENABLED=true

QUICK_ANSWER_MODEL_TIER_FAST_ID=lmstudio/phi-4-mini-reasoning
QUICK_ANSWER_MODEL_TIER_BASIC_ID=lmstudio/qwen3-14b
QUICK_ANSWER_MODEL_TIER_CAPABLE_ID=lmstudio/qwen3-32b
QUICK_ANSWER_MODEL_TIER_SMART_ID=lmstudio/oss-120b
QUICK_ANSWER_MODEL_TIER_GENIUS_ID=
```

## Tier Resolution Rules

### Required behavior

When a recommendation is returned, resolve it to a configured model ID at the recommended tier or the nearest less powerful configured tier.

### Resolution order

If recommendation is:

1. `FAST`
   - try `FAST`
2. `BASIC`
   - try `BASIC`, then `FAST`
3. `CAPABLE`
   - try `CAPABLE`, then `BASIC`, then `FAST`
4. `SMART`
   - try `SMART`, then `CAPABLE`, then `BASIC`, then `FAST`
5. `GENIUS`
   - try `GENIUS`, then `SMART`, then `CAPABLE`, then `BASIC`, then `FAST`

If no configured model ID is found in the allowed fallback chain, no `/model` directive is added.

### Suggested helper contract

```python
def resolve_recommended_model_id(recommendation: str, config: Config) -> str | None:
    ...
```
```

## Upstream Send Rewriting

### Exact behavior

If Stage 2 returns a model recommendation and `resolve_recommended_model_id()` returns a model ID, the outbound upstream text must be rewritten to:

```text
/model <resolved-model-id> <user transcript>
```

### Examples

Input transcript:

```text
what is the tallest building in the world
```

Recommendation:

```json
{
  "type": "model_recommendation",
  "recommendation": "SMART"
}
```

Resolved model:

```text
lmstudio/oss-120b
```

Outbound message:

```text
/model lmstudio/oss-120b what is the tallest building in the world
```

If no resolved model exists, outbound message stays:

```text
what is the tallest building in the world
```

## Prompt Requirements

### Local quick-answer prompt

The quick-answer system prompt must state all of the following explicitly:

1. Built-in voice skills exist for timers, alarms, music, recorder, and new session.
2. If a request maps to one of those skills, the model must emit tool calls only.
3. If the request should go upstream, the model must emit only a `model_recommendation` JSON object.
4. The model must never produce free-form answer prose.
5. The only valid recommendation values are `FAST`, `BASIC`, `CAPABLE`, `SMART`, `GENIUS`.

### Upstream voice agent description

The upstream `voice` agent description must also be updated outside this file's repo if it is managed in OpenClaw agent state rather than checked into the voice repo.

Required upstream instruction additions:

1. Assume built-in voice skills are already handled locally when possible.
2. Expect transcripts may arrive with an inline `/model` directive prefix.
3. Do not attempt to emulate local recorder, timer, alarm, music, or new-session behavior when the voice side can handle it.

## Implementation Touchpoints

Primary files expected to change during implementation:

1. `orchestrator/gateway/quick_answer.py`
2. `orchestrator/config.py`
3. `orchestrator/main.py`
4. `.env.example`
5. `.env.pi.example`
6. `.env.docker.example`
7. `orchestrator/gateway/test_quick_answer_sanitization.py`

Possible additional test files:

1. new quick-answer routing tests
2. recorder fast-path tests
3. upstream message rewrite tests

## Acceptance Criteria

1. A widened set of built-in timer, alarm, music, recorder, and new-session phrases route locally with no LLM call.
2. Recorder commands are supported end-to-end in both deterministic routing and quick-answer tool fallback.
3. The quick-answer LLM never returns spoken prose to TTS.
4. The quick-answer parser accepts only built-in tool calls or `model_recommendation` JSON.
5. Model recommendations resolve to the configured tier or nearest less powerful configured tier.
6. If no configured tier exists, no `/model` prefix is sent.
7. Upstream transcripts include `/model <id>` only when a resolved model exists.
8. Existing built-in skill execution still returns the skill-generated TTS response.

## Required Tests

### Deterministic routing tests

1. timer phrase variants route locally
2. alarm phrase variants route locally
3. music phrase variants route locally
4. `start recording` routes locally when recorder enabled
5. `stop recording` routes locally when recorder enabled
6. `start recording` does not route locally when recorder disabled
7. `stop recording` does not route locally when recorder disabled
8. Recorder variations like `begin recording`, `end recording` do NOT match the recorder skill
9. `start new chat` routes locally when new-session enabled
10. `start new session` routes locally when new-session enabled
11. `start a new chat` routes locally when new-session enabled
12. New-session variations like `new chat`, `fresh session`, `start over` do NOT match the new-session skill
13. Current time/date questions route to quick-answer (not upstream)
14. Simple date arithmetic (e.g. "what day is tomorrow") routes to quick-answer
15. Calendar/event lookup questions still escalate upstream

### Quick-answer contract tests

1. valid tool calls are accepted
2. valid `model_recommendation` JSON is accepted
3. plain prose is rejected
4. malformed JSON is rejected
5. invalid recommendation tier is rejected
6. mixed tool-call and recommendation response is rejected

### Tier mapping tests

1. exact tier match resolves correctly
2. recommendation falls back downward correctly
3. no configured lower tier returns `None`

### Upstream rewrite tests

1. resolved model adds inline `/model` prefix
2. unresolved model sends plain transcript
3. original transcript text remains intact after the prefix

## Recommended Internal Enums

```python
SkillName = Literal["timers", "alarms", "music", "recorder", "new_session"]
RecommendationTier = Literal["FAST", "BASIC", "CAPABLE", "SMART", "GENIUS"]
```
```

## Migration Notes

1. Preserve existing timer, alarm, music, recorder, and new-session tool names where possible.
2. Preserve skill-generated speech strings for local execution.
3. Remove any remaining quick-answer fallback path that treats recorder as local when disabled.
4. Prefer inline `/model` prefixing over upstream protocol changes for the first implementation.
