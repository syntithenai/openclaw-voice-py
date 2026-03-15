#!/usr/bin/env python3
"""Benchmark LM Studio MiniMax latency as quick-answer context grows.

This script mirrors the OpenClaw quick-answer payload shape as closely as is
practical:
- system prompt from orchestrator.gateway.quick_answer
- tool definitions enabled for both timers and music
- up to 10 recent history messages, with the same per-role character caps
- a short fixed user query

It talks directly to the LM Studio native chat endpoint (`/api/v0/chat/completions`)
so it can capture LM Studio's built-in timing stats.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.gateway.quick_answer import (  # noqa: E402
    _HISTORY_ASSISTANT_CHAR_LIMIT,
    _HISTORY_MAX_TURNS,
    _HISTORY_USER_CHAR_LIMIT,
    build_system_prompt,
    build_tool_definitions,
)

FIXED_USER_QUERY = "Reply with exactly ACK. Do not add anything else."
CALIBRATION_MAX_TOKENS = 1
BENCHMARK_MAX_TOKENS = 96
DEFAULT_REPEATS = 3
DEFAULT_ENDPOINT = "http://localhost:1234/api/v0/chat/completions"
DEFAULT_MODEL = "minimax-m2.5"
TARGET_MIN_ADDED_TOKENS = 10


@dataclass
class Sample:
    added_context_tokens: int
    prompt_tokens: int
    completion_tokens: int
    wall_s: float
    ttft_s: float
    generation_s: float
    tps: float
    prompt_processing_est_s: float


@dataclass
class Aggregate:
    target_added_context_tokens: int
    actual_added_context_tokens: int
    prompt_tokens_mean: float
    completion_tokens_mean: float
    wall_s_mean: float
    wall_s_stdev: float
    ttft_s_mean: float
    ttft_s_stdev: float
    generation_s_mean: float
    tps_mean: float
    tps_stdev: float
    prompt_processing_est_s_mean: float
    repeats: int


def filler_text(char_count: int) -> str:
    if char_count <= 0:
        return ""
    seed = (
        "context memory factual concise benchmark latency token window "
        "history message assistant user timing response "
    )
    out = []
    total = 0
    while total < char_count:
        out.append(seed)
        total += len(seed)
    text = ("".join(out))[:char_count]
    if not text.endswith("."):
        text = text[:-1] + "." if len(text) > 1 else "x"
    return text


def build_history_from_char_budget(char_budget: int) -> list[dict[str, Any]]:
    if char_budget <= 0:
        return []

    # Use a realistic alternating conversation shape.
    role_sequence = [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ][: _HISTORY_MAX_TURNS]

    history: list[dict[str, Any]] = []
    remaining = char_budget
    for idx, role in enumerate(role_sequence, start=1):
        if remaining <= 0:
            break
        limit = _HISTORY_USER_CHAR_LIMIT if role == "user" else _HISTORY_ASSISTANT_CHAR_LIMIT
        take = min(limit, remaining)
        history.append(
            {
                "role": role,
                "segment_kind": "final",
                "text": filler_text(take),
                "id": idx,
            }
        )
        remaining -= take
    return history


def build_messages(history: list[dict[str, Any]], current_datetime: str) -> list[dict[str, Any]]:
    system_prompt = build_system_prompt(current_datetime, timers_enabled=True, music_enabled=True)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": item["role"], "content": item["text"]})
    messages.append({"role": "user", "content": FIXED_USER_QUERY})
    return messages


def build_payload(model: str, history: list[dict[str, Any]], current_datetime: str, *, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "messages": build_messages(history, current_datetime),
        "temperature": 0,
        "max_tokens": max_tokens,
        "tools": build_tool_definitions(timers_enabled=True, music_enabled=True),
        "tool_choice": "auto",
    }


def post_chat(endpoint: str, payload: dict[str, Any], timeout: float) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    response = requests.post(endpoint, json=payload, timeout=timeout)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    return elapsed, response.json()


def extract_sample(target_added_context_tokens: int, wall_s: float, data: dict[str, Any], base_prompt_tokens: int) -> Sample:
    usage = data.get("usage", {}) or {}
    stats = data.get("stats", {}) or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    ttft_s = float(stats.get("time_to_first_token", 0.0) or 0.0)
    generation_s = float(stats.get("generation_time", 0.0) or 0.0)
    tps = float(stats.get("tokens_per_second", 0.0) or 0.0)
    prompt_processing_est_s = max(0.0, wall_s - generation_s)
    return Sample(
        added_context_tokens=max(0, prompt_tokens - base_prompt_tokens),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        wall_s=wall_s,
        ttft_s=ttft_s,
        generation_s=generation_s,
        tps=tps,
        prompt_processing_est_s=prompt_processing_est_s,
    )


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def aggregate(target_added_context_tokens: int, samples: list[Sample]) -> Aggregate:
    return Aggregate(
        target_added_context_tokens=target_added_context_tokens,
        actual_added_context_tokens=round(mean([s.added_context_tokens for s in samples])),
        prompt_tokens_mean=mean([s.prompt_tokens for s in samples]),
        completion_tokens_mean=mean([s.completion_tokens for s in samples]),
        wall_s_mean=mean([s.wall_s for s in samples]),
        wall_s_stdev=stdev([s.wall_s for s in samples]),
        ttft_s_mean=mean([s.ttft_s for s in samples]),
        ttft_s_stdev=stdev([s.ttft_s for s in samples]),
        generation_s_mean=mean([s.generation_s for s in samples]),
        tps_mean=mean([s.tps for s in samples]),
        tps_stdev=stdev([s.tps for s in samples]),
        prompt_processing_est_s_mean=mean([s.prompt_processing_est_s for s in samples]),
        repeats=len(samples),
    )


def find_prompt_tokens_for_budget(
    endpoint: str,
    model: str,
    current_datetime: str,
    char_budget: int,
    timeout: float,
    cache: dict[int, int],
) -> int:
    if char_budget in cache:
        return cache[char_budget]
    payload = build_payload(model, build_history_from_char_budget(char_budget), current_datetime, max_tokens=CALIBRATION_MAX_TOKENS)
    _, data = post_chat(endpoint, payload, timeout)
    prompt_tokens = int((data.get("usage", {}) or {}).get("prompt_tokens", 0) or 0)
    cache[char_budget] = prompt_tokens
    return prompt_tokens


def calibrate_char_budget_for_target_added_tokens(
    endpoint: str,
    model: str,
    current_datetime: str,
    target_added_tokens: int,
    base_prompt_tokens: int,
    max_char_budget: int,
    timeout: float,
    cache: dict[int, int],
) -> tuple[int, int]:
    low = 0
    high = max_char_budget
    best_budget = 0
    best_delta = math.inf
    target_prompt_tokens = base_prompt_tokens + target_added_tokens

    while low <= high:
        mid = (low + high) // 2
        prompt_tokens = find_prompt_tokens_for_budget(endpoint, model, current_datetime, mid, timeout, cache)
        delta = abs(prompt_tokens - target_prompt_tokens)
        if delta < best_delta:
            best_delta = delta
            best_budget = mid
        if prompt_tokens < target_prompt_tokens:
            low = mid + 1
        elif prompt_tokens > target_prompt_tokens:
            high = mid - 1
        else:
            best_budget = mid
            break

    achieved_prompt_tokens = find_prompt_tokens_for_budget(endpoint, model, current_datetime, best_budget, timeout, cache)
    achieved_added_tokens = max(0, achieved_prompt_tokens - base_prompt_tokens)
    return best_budget, achieved_added_tokens


def build_target_levels(max_added_tokens: int) -> list[int]:
    if max_added_tokens <= TARGET_MIN_ADDED_TOKENS:
        return [max_added_tokens]
    levels = [TARGET_MIN_ADDED_TOKENS]
    candidate = TARGET_MIN_ADDED_TOKENS
    while candidate < max_added_tokens:
        candidate = max(candidate + 10, int(round(candidate * 1.6)))
        levels.append(min(candidate, max_added_tokens))
        if levels[-1] == max_added_tokens:
            break
    if levels[-1] != max_added_tokens:
        levels.append(max_added_tokens)
    return sorted(set(levels))


def format_table(rows: list[Aggregate]) -> str:
    headers = [
        "target_ctx",
        "actual_ctx",
        "prompt_toks",
        "prefill_est_s",
        "ttft_s",
        "tok_s",
        "wall_s",
    ]
    widths = [11, 10, 11, 13, 8, 8, 8]

    def fmt_row(cols: list[str]) -> str:
        return " ".join(col.rjust(width) for col, width in zip(cols, widths))

    lines = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    for row in rows:
        lines.append(
            fmt_row(
                [
                    str(row.target_added_context_tokens),
                    str(row.actual_added_context_tokens),
                    f"{row.prompt_tokens_mean:.0f}",
                    f"{row.prompt_processing_est_s_mean:.3f}",
                    f"{row.ttft_s_mean:.3f}",
                    f"{row.tps_mean:.1f}",
                    f"{row.wall_s_mean:.3f}",
                ]
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    cache: dict[int, int] = {}

    base_prompt_tokens = find_prompt_tokens_for_budget(
        args.endpoint,
        args.model,
        current_datetime,
        0,
        args.timeout,
        cache,
    )

    realistic_max_char_budget = ((_HISTORY_MAX_TURNS + 1) // 2) * _HISTORY_USER_CHAR_LIMIT + (_HISTORY_MAX_TURNS // 2) * _HISTORY_ASSISTANT_CHAR_LIMIT
    max_prompt_tokens = find_prompt_tokens_for_budget(
        args.endpoint,
        args.model,
        current_datetime,
        realistic_max_char_budget,
        args.timeout,
        cache,
    )
    max_added_tokens = max(0, max_prompt_tokens - base_prompt_tokens)
    target_levels = build_target_levels(max_added_tokens)

    print(f"Model: {args.model}")
    print(f"Endpoint: {args.endpoint}")
    print(f"Base prompt tokens (system + tools + fixed query): {base_prompt_tokens}")
    print(f"Likely max added context tokens (10 capped history messages): {max_added_tokens}")
    print(f"Likely max total prompt tokens: {max_prompt_tokens}")
    print(f"Repeats per level: {args.repeats}")
    print("")

    results: list[Aggregate] = []
    raw_results: list[dict[str, Any]] = []

    for target in target_levels:
        char_budget, achieved_added_tokens = calibrate_char_budget_for_target_added_tokens(
            args.endpoint,
            args.model,
            current_datetime,
            target,
            base_prompt_tokens,
            realistic_max_char_budget,
            args.timeout,
            cache,
        )
        history = build_history_from_char_budget(char_budget)
        samples: list[Sample] = []
        for _ in range(args.repeats):
            payload = build_payload(args.model, history, current_datetime, max_tokens=BENCHMARK_MAX_TOKENS)
            wall_s, data = post_chat(args.endpoint, payload, args.timeout)
            sample = extract_sample(target, wall_s, data, base_prompt_tokens)
            samples.append(sample)
            raw_results.append(
                {
                    "target_added_context_tokens": target,
                    "achieved_added_context_tokens": sample.added_context_tokens,
                    "char_budget": char_budget,
                    "prompt_tokens": sample.prompt_tokens,
                    "completion_tokens": sample.completion_tokens,
                    "wall_s": sample.wall_s,
                    "ttft_s": sample.ttft_s,
                    "generation_s": sample.generation_s,
                    "tokens_per_second": sample.tps,
                    "prompt_processing_est_s": sample.prompt_processing_est_s,
                }
            )
        results.append(aggregate(target, samples))
        print(
            f"Finished target={target} added tokens -> actual≈{achieved_added_tokens} | "
            f"prompt≈{results[-1].prompt_tokens_mean:.0f} tokens"
        )

    print("")
    print(format_table(results))

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "endpoint": args.endpoint,
                    "base_prompt_tokens": base_prompt_tokens,
                    "max_added_context_tokens": max_added_tokens,
                    "max_prompt_tokens": max_prompt_tokens,
                    "aggregates": [row.__dict__ for row in results],
                    "samples": raw_results,
                },
                indent=2,
            )
        )
        print(f"\nWrote JSON results to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
