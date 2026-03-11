# Plan: Add Latency Tracking for Quick Response Requests

## Objective
Add latency measurement and logging for quick answer LLM requests to match the existing gateway request latency tracking.

## Changes Made

### 1. Track Quick Answer Request Timing
- Start timer before `get_quick_answer()` call using `time.monotonic()`
- Calculate elapsed time in milliseconds after response
- Store in `qa_elapsed` variable (mirrors `gw_elapsed` for gateway)

### 2. Log Latency on Success
When quick answer provides a response, display latency in both:
- **Logger**: `✓ QUICK ANSWER: Using LLM response instead of gateway (latency: %dms)`
- **Console**: `← QUICK ANSWER: {response} [latency: {qa_elapsed}ms]`

### 3. Log Latency on Escalation
When LLM returns `USE_UPSTREAM_AGENT` and escalates to gateway:
- **Logger**: `← QUICK ANSWER: Escalating to upstream (latency: %dms)`

### 4. Log Latency on Error
In exception handler:
- Capture elapsed time if `qa_start` was set
- **Logger**: `Quick answer failed: %s; falling back to gateway (latency: %dms)`

## Implementation Details

**File Modified:** `orchestrator/main.py`

**Lines Modified:** ~1127-1165

**Pattern Used:** Same timing pattern as gateway request (lines ~1172-1179):
```python
qa_start = time.monotonic()
should_use_upstream, quick_response = await quick_answer_client.get_quick_answer(combined_transcript)
qa_elapsed = int((time.monotonic() - qa_start) * 1000)
```

## Benefits
- **Consistency**: Matches gateway latency tracking pattern
- **Visibility**: Performance metrics visible in both logs and console
- **Debugging**: Helps identify bottlenecks in quick answer vs. full gateway paths
- **Comparison**: Enables direct comparison of response times between quick LLM and upstream agent

## Testing
Run orchestrator and verify:
1. Quick answer success path displays latency in console and logs
2. Escalation to upstream logs latency before gateway call
3. Error path shows latency before fallback to gateway
4. Latency values are reasonable (typically < 1000ms for quick answers)

## Related Code
- Gateway latency tracking: `orchestrator/main.py` ~line 1179
- Quick answer client: `orchestrator/gateway/quick_answer.py`
- Configuration: `QUICK_ANSWER_TIMEOUT_MS` in `orchestrator/config.py`
