"""Quick answer LLM client for fast factual responses."""
import asyncio
import json
import logging
import re
import httpx
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence


logger = logging.getLogger("orchestrator.gateway.quick_answer")

# Thinking phrases to play when escalating to gateway
THINKING_PHRASES = [
    "thinking",
    "just a sec",
    "onto it",
    "let me think",
    "one moment",
    "hmm",
    "let me check",
    "give me a sec",
    "working on it",
]


def get_random_thinking_phrase() -> str:
    """Get a random thinking phrase for gateway escalation."""
    return random.choice(THINKING_PHRASES)


def _preview(value: object, limit: int = 100) -> str:
    """Safe string preview for logging that never assumes sliceable types."""
    return str(value)[:limit]


def _extract_spoken_text_candidate(value: object) -> str:
    """Extract best-effort human-friendly speech text from mixed payloads."""
    if value is None:
        return ""

    if isinstance(value, dict):
        # Preferred direct message keys
        for key in ("response", "text", "content", "message", "error", "label", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate

        # Common nested tool payload wrappers
        for nested_key in ("result", "data"):
            if nested_key in value:
                nested_candidate = _extract_spoken_text_candidate(value.get(nested_key))
                if nested_candidate:
                    return nested_candidate

        # Last resort for dicts: avoid speaking full JSON blobs
        return ""

    if isinstance(value, list):
        parts = [_extract_spoken_text_candidate(item) for item in value]
        return " ".join(part for part in parts if part)

    return str(value)


def sanitize_quick_answer_text(text: object) -> str:
    """Normalize quick-answer content for speech-friendly playback.

    Accepts either a plain string or structured tool-router payloads and returns
    spoken text with markdown emphasis markers removed.
    """
    candidate = _extract_spoken_text_candidate(text)

    candidate = str(candidate)
    cleaned = candidate.replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _word_tokens(text: str) -> list[str]:
    """Extract spoken-word-like tokens from text for stable length checks."""
    return re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", text or "")


def _truncate_to_target_words(text: str, target_words: int) -> str:
    """Truncate a string to at most target_words using spoken-like tokenization."""
    if target_words <= 0:
        return ""
    tokens = _word_tokens(text)
    if not tokens:
        return ""
    return " ".join(tokens[:target_words]).strip()


def resolve_recommended_model_id(recommendation: Any, config: Any) -> Optional[str]:
    """
    Resolve a recommended model ID from a quick-answer model recommendation dict.

    Args:
        recommendation: Dict with optional 'tier' key ('fast', 'basic', 'capable', 'smart', 'genius')
        config: Config object with tier model ID fields

    Returns:
        Resolved model ID string, or None if no tier mapping exists or tier is invalid.

    Resolution order:
        1. Check if recommendation has a 'tier' key matching a known tier
        2. Return the corresponding config field value if non-empty
        3. If not found or empty, try next tier in fallback chain
        4. Return None if no tier or no valid model ID found

    Fallback chain: fast → basic → capable → smart → genius
    """
    if not isinstance(recommendation, dict):
        return None

    tier = recommendation.get("tier")
    if not isinstance(tier, str):
        return None

    # Map tier names to config field names
    tier_to_field = {
        "fast": "quick_answer_model_tier_fast_id",
        "basic": "quick_answer_model_tier_basic_id",
        "capable": "quick_answer_model_tier_capable_id",
        "smart": "quick_answer_model_tier_smart_id",
        "genius": "quick_answer_model_tier_genius_id",
    }

    # Fallback order if the requested tier is empty
    fallback_order = ["fast", "basic", "capable", "smart", "genius"]

    # Start from the requested tier or the beginning of the chain
    try:
        start_idx = fallback_order.index(tier)
    except ValueError:
        return None  # Invalid tier name

    # Walk the fallback chain
    for fallback_tier in fallback_order[start_idx:]:
        field_name = tier_to_field[fallback_tier]
        model_id = getattr(config, field_name, None)
        if isinstance(model_id, str) and model_id.strip():
            return model_id.strip()

    return None


QUICK_ANSWER_BASE_SYSTEM_PROMPT = """You are a strict validation gatekeeper. Your sole objective is to handle tool-eligible requests with the provided tools and escalate everything else.

Strict Response Protocol:
- For tool-eligible requests (timers, alarms, music, recording, session management): invoke the relevant provided tool. Do not explain, confirm, or describe what you are doing - just call the tool.
- For legitimate questions requiring a more capable model: respond with exactly this JSON (no other text): {{"type": "model_recommendation", "tier": "TIER", "reason": "brief explanation"}} where TIER is one of: fast, basic, capable, smart, genius.
- For all other queries: respond with exactly: USE_UPSTREAM_AGENT

NO FREE-FORM PROSE: Never respond with explanatory text, apologies, or conversational replies. Your only valid text responses are USE_UPSTREAM_AGENT or the model_recommendation JSON. For tool requests, call the tool.

Content Rules:
- Questions about the current date or time, plus simple calculations based only on the provided current date and time, are allowed. Answer these directly in one short sentence.
- If the user is asking about personal data, account-specific state, email, inbox contents, notifications, messages, calendar items, or anything that depends on prior conversation context or external state, respond with: USE_UPSTREAM_AGENT.
- If the user references earlier dialogue with phrases like "you never told me", "what about", "did I get", "any new ones", "check my", or "do I have", respond with: USE_UPSTREAM_AGENT unless a timer/alarm/music/session tool directly answers it.

Current date and time: {current_datetime}

{tool_usage_section}"""

QUICK_ANSWER_BASE_SYSTEM_PROMPT_NO_MODELS = """You are a strict validation gatekeeper. Your sole objective is to handle tool-eligible requests with the provided tools and escalate everything else.

Strict Response Protocol:
- For tool-eligible requests (timers, alarms, music, recording, session management): invoke the relevant provided tool. Do not explain, confirm, or describe what you are doing - just call the tool.
- For all other queries: respond with exactly: USE_UPSTREAM_AGENT

NO FREE-FORM PROSE: Never respond with explanatory text, apologies, or conversational replies. Your only valid text responses are USE_UPSTREAM_AGENT. For tool requests, call the tool.

Content Rules:
- If the user is asking about personal data, account-specific state, email, inbox contents, notifications, messages, calendar items, or anything that depends on prior conversation context or external state, respond with: USE_UPSTREAM_AGENT.
- If the user references earlier dialogue, respond with: USE_UPSTREAM_AGENT unless a timer/alarm/music/session tool directly answers it.

Current date and time: {current_datetime}

{tool_usage_section}"""


def build_tool_usage_section(
    timers_enabled: bool,
    music_enabled: bool,
    recorder_enabled: bool,
    new_session_enabled: bool,
    openclaw_models_available: bool = True,
) -> str:
    """Build a prompt section that only mentions tool families that are actually available."""
    sections: list[str] = []

    if timers_enabled or music_enabled or recorder_enabled or new_session_enabled:
        sections.append("Available tools (invoke the tool for matching requests):")
        if timers_enabled and music_enabled and recorder_enabled:
            sections.append("- Timer/alarm requests → call timer or alarm tools")
            sections.append("- Music control requests → call music tools")
            sections.append("- Recording requests → call recorder tool")
        elif timers_enabled and music_enabled:
            sections.append("- Timer/alarm requests → call timer or alarm tools")
            sections.append("- Music control requests → call music tools")
        elif timers_enabled and recorder_enabled:
            sections.append("- Timer/alarm requests → call timer or alarm tools")
            sections.append("- Recording requests → call recorder tool")
        elif music_enabled and recorder_enabled:
            sections.append("- Music control requests → call music tools")
            sections.append("- Recording requests → call recorder tool")
        elif timers_enabled:
            sections.append("- Timer/alarm requests → call timer or alarm tools")
        elif music_enabled:
            sections.append("- Music control requests → call music tools")
        elif recorder_enabled:
            sections.append("- Recording requests → call recorder tool")
        if new_session_enabled:
            sections.append("- Session management requests (start new chat/session) → call session tool")

    if openclaw_models_available:
        sections.append("- All other queries → respond with model_recommendation JSON or USE_UPSTREAM_AGENT")
    else:
        sections.append("- All other queries → respond with USE_UPSTREAM_AGENT")
    return "\n".join(sections)


def build_system_prompt(
    current_datetime: str,
    timers_enabled: bool,
    music_enabled: bool,
    recorder_enabled: bool,
    new_session_enabled: bool,
    openclaw_models_available: bool = True,
) -> str:
    """
    Build the system prompt for the current tool capabilities.
    
    Args:
        current_datetime: Current date and time string
        timers_enabled: Whether timer/alarm tools are available
        music_enabled: Whether music control tools are available
        recorder_enabled: Whether recording tools are available
        new_session_enabled: Whether new-session tools are available
        openclaw_models_available: Whether OpenClaw gateway has configured models.
            When False, uses the no-models contract (no model_recommendation option).
    """
    base_prompt = (
        QUICK_ANSWER_BASE_SYSTEM_PROMPT 
        if openclaw_models_available 
        else QUICK_ANSWER_BASE_SYSTEM_PROMPT_NO_MODELS
    )
    return base_prompt.format(
        current_datetime=current_datetime,
        tool_usage_section=build_tool_usage_section(
            timers_enabled,
            music_enabled,
            recorder_enabled,
            new_session_enabled,
            openclaw_models_available,
        ),
    )


# Per-message character caps for history injection.
# Assistant responses are capped tighter because gateway replies can be very long.
_HISTORY_MAX_TURNS = 10
_HISTORY_USER_CHAR_LIMIT = 300
_HISTORY_ASSISTANT_CHAR_LIMIT = 150


def build_history_messages(
    chat_history: list[dict],
    *,
    max_turns: int = _HISTORY_MAX_TURNS,
    user_char_limit: int = _HISTORY_USER_CHAR_LIMIT,
    assistant_char_limit: int = _HISTORY_ASSISTANT_CHAR_LIMIT,
) -> list[dict[str, str]]:
    """Build a trimmed OpenAI-format message list from web service chat history.

    Only includes final user/assistant turns; skips steps, partials, and empty
    entries.  Each message is hard-truncated so history stays compact — assistant
    responses especially can be very verbose.
    """
    filtered = [
        m for m in chat_history
        if m.get("role") in ("user", "assistant")
        and m.get("segment_kind", "final") == "final"
        and m.get("text", "").strip()
    ]
    recent = filtered[-max_turns:]
    result: list[dict[str, str]] = []
    for m in recent:
        role = m["role"]
        text = m.get("text", "").strip()
        limit = user_char_limit if role == "user" else assistant_char_limit
        if len(text) > limit:
            text = text[:limit].rstrip() + "\u2026"
        result.append({"role": role, "content": text})
    return result


UPSTREAM_ONLY_PATTERNS = [
    r"\b(email|emails|inbox|mailbox|gmail|outlook)\b",
    r"\b(notification|notifications|message|messages|text|texts|voicemail|voicemails)\b",
    r"\b(calendar|appointment|appointments|meeting|meetings|schedule)\b",
    r"\bmy\s+(email|emails|inbox|mailbox|calendar|messages|notifications)\b",
    r"\b(do i have|did i get|any new|check my|look at my|what about my)\b",
    r"\b(you never told me|earlier you said|last time|before that|what about that)\b",
]

# Time-sensitive or web-lookup intents should go upstream so model/tool routing
# can fetch fresh information and provide provenance.
WEB_LOOKUP_PATTERNS = [
    r"\b(find|search|look up|lookup|google|browse)\b.*\b(web ?page|website|site|url|link|source)\b",
    r"\b(web ?page|website|site|url|link|source)\b",
]

TIME_SENSITIVE_PATTERNS = [
    r"\b(current|currently|latest|today|now|this week|this month|this year|as of)\b",
    r"\b(who is the president|president of the (u\.?s\.?|united states))\b",
]

DATE_TIME_QUICK_ANSWER_PATTERNS = [
    r"\bwhat(?:'s|\s+is)?\s+(?:the\s+)?(?:current\s+)?time\b",
    r"\bwhat(?:'s|\s+is)?\s+(?:the\s+)?(?:current\s+)?date\b",
    r"\bwhat(?:'s|\s+is)?\s+today'?s\s+date\b",
    r"\bwhat\s+day\s+is\s+it(?:\s+(?:today|now))?\b",
    r"\bwhat\s+(?:day|date|time)\s+(?:is\s+it|will\s+it\s+be)\b.*\b(?:today|tomorrow|yesterday|now|in\s+\d+|after\s+\d+|from\s+now|from\s+today|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this\s+(?:afternoon|evening|morning|week|month|year))\b",
    r"\bhow\s+many\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?)\b.*\b(?:until|from\s+now|from\s+today|before|after)\b",
]

# Action/task intents should go upstream so tools/agents can execute the request.
ACTION_INTENT_PATTERNS = [
    r"\b(add|append|put|include)\b.*\b(shopping list|grocery list|todo list|to-do list|list)\b",
    r"\b(shopping|grocery)\b.*\b(add|buy|get|pick up)\b",
    r"^\s*(also\s+)?add\b",
    r"\b(remind me|set up|create|book|schedule|order|send|message|email|call)\b",
    r"\b(open|launch|start)\b.*\b(browser|web\s*browser|tab|window)\b",
    r"\b(open|go to|navigate to|visit)\b\s+([\w-]+\.)+[a-z]{2,}\b",
]

# Transcript retrieval/summarization should be handled upstream so the
# video-transcript-downloader skill can run instead of recorder actions.
TRANSCRIPT_INTENT_PATTERNS = [
    r"\btranscript(?:s)?\b",
    r"\bcaption(?:s)?\b",
    r"\bsubtitle(?:s)?\b",
    r"\byoutube\b.*\b(transcript|caption|subtitle)\b",
    r"\b(video|youtube)\b.*\b(download|get|fetch|pull|extract)\b.*\b(transcript|captions?|subtitles?)\b",
]

# Long-form authoring/research requests should bypass quick answer and go upstream.
DOCUMENT_AUTHORING_PATTERNS = [
    r"\b(write|draft|create|generate|prepare|compile)\b.*\b(document|doc|report|plan|lesson\s*plan|outline)\b",
    r"\b(save|export)\b.*\b(document|doc|file|markdown|md|pdf|text)\b",
    r"\b(write|put|save)\b.*\bto\b.*\b(document|doc|file)\b",
    r"\bresearch\b.*\b(write|document|report|plan|lesson\s*plan|outline)\b",
    r"\b(write|compose|draft|tell)\b.*\b(story|stories|tale|poem|essay|article|speech|script)\b",
    r"\b(write|compose|draft)\b.*\b\d+\s*[\-\s]?word\b",
]

RECORDER_INTENT_PATTERNS = [
    r"\bstart\s+(the\s+)?record(ing)?\b",
    r"\bstop\s+(the\s+)?record(ing)?\b",
]

NEW_SESSION_INTENT_PATTERNS = [
    r"\bstart\s+(?:a\s+)?new\s+(?:session|chat)\b",
]

TIMER_ALARM_INTENT_PATTERNS = [
    r"\b(timer|timers|alarm|alarms|countdown)\b",
    r"\b(set|add|create|start|cancel|stop|list|show|delete|remove)\b.*\b(timer|alarm)\b",
    r"\b(in\s+\d+\s*(seconds?|minutes?|hours?))\b",
]

MUSIC_INTENT_PATTERNS = [
    r"\b(music|song|songs|track|tracks|playlist|album|artist)\b",
    r"\b(play|pause|resume|skip|next|previous|stop)\b.*\b(music|song|track|playlist)\b",
    r"\b(queue|queued|playlist|playlists)\b",
    r"\b(add|remove|change|replace|load|clear|shuffle)\b.*\b(queue|playlist|song|songs|track|tracks)\b",
]


def classify_upstream_decision(
    user_query: str,
    *,
    timers_enabled: bool = False,
    music_enabled: bool = False,
    recorder_enabled: bool = False,
    new_session_enabled: bool = False,
) -> tuple[bool, str]:
    """Classify whether a query should bypass quick answer and why."""
    query = user_query.strip().lower()
    if not query:
        return True, "empty_query"

    if any(re.search(pattern, query) for pattern in UPSTREAM_ONLY_PATTERNS):
        return True, "context_or_account_specific"

    if any(re.search(pattern, query) for pattern in WEB_LOOKUP_PATTERNS):
        return True, "web_lookup"

    if any(re.search(pattern, query) for pattern in DATE_TIME_QUICK_ANSWER_PATTERNS):
        return False, "date_time_local"

    if any(re.search(pattern, query) for pattern in TIME_SENSITIVE_PATTERNS):
        return True, "time_sensitive"

    if any(re.search(pattern, query) for pattern in DOCUMENT_AUTHORING_PATTERNS):
        return True, "document_authoring"

    if any(re.search(pattern, query) for pattern in TRANSCRIPT_INTENT_PATTERNS):
        return True, "transcript_upstream"

    # If timers/alarms or music tooling is available, keep those intents local.
    if timers_enabled and any(re.search(pattern, query) for pattern in TIMER_ALARM_INTENT_PATTERNS):
        return False, "timer_alarm_local"

    if music_enabled and any(re.search(pattern, query) for pattern in MUSIC_INTENT_PATTERNS):
        return False, "music_local"

    recorder_intent = any(re.search(pattern, query) for pattern in RECORDER_INTENT_PATTERNS)
    if recorder_enabled and recorder_intent:
        return False, "recorder_local"

    new_session_intent = any(re.search(pattern, query) for pattern in NEW_SESSION_INTENT_PATTERNS)
    if new_session_enabled and new_session_intent:
        return False, "new_session_local"
    if new_session_intent:
        return True, "new_session_action_disabled"

    if any(re.search(pattern, query) for pattern in ACTION_INTENT_PATTERNS):
        return True, "action_intent"

    return False, "quick_answer_allowed"


def should_force_upstream(
    user_query: str,
    *,
    timers_enabled: bool = False,
    music_enabled: bool = False,
    recorder_enabled: bool = False,
    new_session_enabled: bool = False,
) -> bool:
    """Return True for queries that should bypass quick answer and go upstream."""
    decision, _reason = classify_upstream_decision(
        user_query,
        timers_enabled=timers_enabled,
        music_enabled=music_enabled,
        recorder_enabled=recorder_enabled,
        new_session_enabled=new_session_enabled,
    )
    return decision


TIMER_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Set a countdown timer that will alert when it expires",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "number",
                        "description": "Duration in seconds for the timer"
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the timer (e.g., 'pasta', 'workout')"
                    }
                },
                "required": ["duration_seconds"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_timer",
            "description": "Cancel a specific timer by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the timer to cancel"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_all_timers",
            "description": "Cancel all active timers",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_timers",
            "description": "List all active timers",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": "Set an alarm for a specific time",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_str": {
                        "type": "string",
                        "description": "Time as absolute clock time ('6:30 AM', '18:30', 'tomorrow 9am') or relative duration ('in 2 hours', 'in 30 minutes', 'in 10 seconds'). Always include the word 'in' for relative durations."
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the alarm (e.g., 'wake up', 'meeting')"
                    }
                },
                "required": ["time_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_alarm",
            "description": "Cancel a future alarm by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the alarm to cancel"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_alarm",
            "description": "Stop a currently ringing alarm",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional name of alarm to stop. If omitted, stops all ringing alarms."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_alarms",
            "description": "List all active alarms",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]


MUSIC_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "music_play",
            "description": "Start or resume music playback",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_pause",
            "description": "Pause music playback",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_stop",
            "description": "Stop music playback completely",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_next",
            "description": "Skip to the next track",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_previous",
            "description": "Go to the previous track",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_set_volume",
            "description": "Set music volume level",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "number",
                        "description": "Volume level from 0-100"
                    }
                },
                "required": ["level"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_get_current",
            "description": "Get information about the currently playing track",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_get_status",
            "description": "Get current music player status (playing/paused/stopped)",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_play_artist",
            "description": "Play music by a specific artist",
            "parameters": {
                "type": "object",
                "properties": {
                    "artist": {
                        "type": "string",
                        "description": "Name of the artist"
                    },
                    "shuffle": {
                        "type": "boolean",
                        "description": "Whether to shuffle the tracks (default: true)"
                    }
                },
                "required": ["artist"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_play_album",
            "description": "Play a specific album",
            "parameters": {
                "type": "object",
                "properties": {
                    "album": {
                        "type": "string",
                        "description": "Name of the album"
                    }
                },
                "required": ["album"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_play_genre",
            "description": "Replace the current queue with music from a genre and start playing. Use for 'play some blues', 'play jazz'. Do NOT use when the user says 'add' songs to the queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {
                        "type": "string",
                        "description": "Music genre (e.g., rock, jazz, classical)"
                    },
                    "shuffle": {
                        "type": "boolean",
                        "description": "Whether to shuffle the tracks (default: true)"
                    }
                },
                "required": ["genre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_play_song",
            "description": "Play a specific song by title",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the song"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_search",
            "description": "Search for music (artist, album, title, or any field)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_add_songs",
            "description": "Add songs to the END of the current queue without clearing it. Use when the user says 'add X songs', 'add some blues songs', 'put more jazz on', etc. Supports genre, artist, title, or any search term.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Genre, artist, title, or search term (e.g. 'blues', 'jazz', 'Bob Dylan')"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of songs to add (default: 5)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_clear_queue",
            "description": "Clear all items from the current queue and detach from any currently loaded saved playlist so subsequent queue edits are not auto-saved to that playlist.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_load_playlist",
            "description": "Load a saved playlist",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the playlist to load"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "music_update_library",
            "description": "Scan music directory and update the library database",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


RECORDER_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "recorder",
            "description": "Control continuous recording and post-processing (whisper transcription + optional pyannote diarization)",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Recorder action: start, stop, or status",
                        "enum": ["start", "stop", "status"],
                    }
                },
                "required": ["action"],
            },
        },
    }
]


SESSION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "start_new_session",
            "description": "Start a brand new chat session, equivalent to pressing the New button in the chat UI",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


def build_tool_definitions(
    timers_enabled: bool,
    music_enabled: bool,
    recorder_enabled: bool,
    new_session_enabled: bool,
) -> list[dict]:
    """Return only the tool definitions that are actually available."""
    tool_definitions: list[dict] = []
    if timers_enabled:
        tool_definitions.extend(TIMER_TOOL_DEFINITIONS)
    if music_enabled:
        tool_definitions.extend(MUSIC_TOOL_DEFINITIONS)
    if recorder_enabled:
        tool_definitions.extend(RECORDER_TOOL_DEFINITIONS)
    if new_session_enabled:
        tool_definitions.extend(SESSION_TOOL_DEFINITIONS)
    return tool_definitions


def _load_json_with_comments(path: Path) -> Any:
    """Load JSON with light JSONC-style support for line comments/trailing commas."""
    text = path.read_text(encoding="utf-8")
    # Remove comment-only lines (e.g. // comment)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    # Remove trailing commas before object/array close
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def _count_models_in_openclaw_config(config: Any) -> int:
    """Best-effort count of configured models in models/openclaw config payloads."""
    if isinstance(config, list):
        return len(config)

    if not isinstance(config, dict):
        return 0

    count = 0

    direct_models = config.get("models")
    if isinstance(direct_models, list):
        count += len(direct_models)

    # Handle openclaw.json shape: models.providers.<provider>.models[]
    models_obj = config.get("models")
    if isinstance(models_obj, dict):
        nested_models = models_obj.get("models")
        if isinstance(nested_models, list):
            count += len(nested_models)
        providers = models_obj.get("providers")
        if isinstance(providers, dict):
            for provider_cfg in providers.values():
                if isinstance(provider_cfg, dict):
                    provider_models = provider_cfg.get("models")
                    if isinstance(provider_models, list):
                        count += len(provider_models)

    # Also support providers at root level
    providers = config.get("providers")
    if isinstance(providers, dict):
        for provider_cfg in providers.values():
            if isinstance(provider_cfg, dict):
                provider_models = provider_cfg.get("models")
                if isinstance(provider_models, list):
                    count += len(provider_models)

    return count


def configured_models_available_from_files(config_paths: Optional[Sequence[str]] = None) -> bool:
    """Return True if any provided models/openclaw config file defines at least one model."""
    if config_paths is None:
        config_paths = [
            str(Path.cwd() / "models.json"),
            str(Path.cwd() / "openclaw.json"),
            str(Path.cwd() / ".openclaw" / "models.json"),
            str(Path.cwd() / ".openclaw" / "openclaw.json"),
            str(Path.cwd().parent / ".openclaw" / "models.json"),
            str(Path.cwd().parent / ".openclaw" / "openclaw.json"),
            str(Path.home() / ".openclaw" / "models.json"),
            str(Path.home() / ".openclaw" / "openclaw.json"),
        ]

    existing_paths: list[Path] = []
    for raw_path in config_paths:
        path = Path(raw_path)
        if path.exists() and path.is_file():
            existing_paths.append(path)

    if not existing_paths:
        logger.info("No models.json/openclaw.json config files found; model recommendations disabled")
        return False

    for path in existing_paths:
        try:
            parsed = _load_json_with_comments(path)
            model_count = _count_models_in_openclaw_config(parsed)
            if model_count > 0:
                logger.info("Configured models found in %s (count=%d)", path, model_count)
                return True
            logger.info("Config file %s has no configured models", path)
        except Exception as exc:
            logger.debug("Failed parsing model config file %s: %s", path, exc)

    logger.info("No configured models found in models.json/openclaw.json files")
    return False


async def check_openclaw_models_available(
    gateway_url: str,
    token: str,
    timeout_s: float = 10,
    config_paths: Optional[Sequence[str]] = None,
) -> bool:
    """
    Check if OpenClaw has configured models available in models.json/openclaw.json.

    This is intentionally config-driven. If we cannot find configured models in
    config files, quick-answer must not ask the LLM for model recommendations.
    
    Args:
        gateway_url: Unused (kept for backward-compatible call sites)
        token: Unused (kept for backward-compatible call sites)
        timeout_s: Unused (kept for backward-compatible call sites)
        config_paths: Optional ordered candidate file paths to check
    
    Returns:
        True if at least one configured model is found, False otherwise
    """
    _ = gateway_url
    _ = token
    _ = timeout_s
    return configured_models_available_from_files(config_paths)


class QuickAnswerClient:
    """Client for getting quick factual answers from an LLM before escalating to the gateway."""
    
    def __init__(
        self,
        llm_url: str,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_ms: int = 5000,
        timers_enabled: bool = False,
        music_enabled: bool = False,
        recorder_enabled: bool = False,
        tool_router = None,
        music_router = None,
        recorder_tool = None,
        web_service = None,
        new_session_handler: Callable[[], Awaitable[str | None]] | None = None,
        openclaw_models_available: bool = True,
    ):
        """
        Initialize the quick answer client.
        
        Args:
            llm_url: OpenAI-compatible chat completions endpoint
            model: Model name to use (e.g., "gpt-3.5-turbo" or LM Studio model name)
            api_key: Optional API key for authentication
            timeout_ms: Request timeout in milliseconds
            timers_enabled: Enable timer/alarm tool support
            music_enabled: Enable music tool support
            tool_router: ToolRouter instance for executing tool calls
            music_router: MusicRouter instance for executing music control calls
            web_service: Web service for sending state updates after music tools
            openclaw_models_available: Whether OpenClaw gateway has configured models available
                When False, model_recommendation will not be offered to the LLM
        """
        self.llm_url = llm_url
        self.model = model or "gpt-3.5-turbo"  # Default fallback
        self.api_key = api_key
        self.timeout_s = timeout_ms / 1000.0
        self.tool_router = tool_router
        self.music_router = music_router
        self.recorder_tool = recorder_tool
        self.web_service = web_service
        self.new_session_handler = new_session_handler
        self.openclaw_models_available = bool(openclaw_models_available)
        self.timers_enabled = bool(timers_enabled and tool_router is not None)
        self.music_enabled = bool(music_enabled and music_router is not None)
        self.recorder_enabled = bool(recorder_enabled and recorder_tool is not None)
        self.new_session_enabled = bool(new_session_handler is not None)
        self.tool_definitions = build_tool_definitions(
            self.timers_enabled,
            self.music_enabled,
            self.recorder_enabled,
            self.new_session_enabled,
        )
        self._last_tool_steps: list[dict[str, str]] = []
        self._last_model_recommendation: dict[str, str] | None = None
        self._voice_music_action_seq: int = 0

    def _new_voice_music_action_id(self) -> str:
        self._voice_music_action_seq += 1
        return f"qa-music-{self._voice_music_action_seq}"

    async def _emit_music_action_pending(self, action: str, action_id: str, *, name: str | None = None) -> None:
        if not self.web_service:
            return
        try:
            payload = {
                "type": "music_action_pending",
                "action": str(action),
                "action_id": str(action_id),
            }
            if name:
                payload["name"] = str(name)
            await self.web_service.broadcast(
                payload
            )
        except Exception as exc:
            logger.debug("Failed to emit music_action_pending: %s", exc)

    async def _emit_music_action_ack(self, action: str, action_id: str) -> None:
        if not self.web_service:
            return
        try:
            await self.web_service.broadcast(
                {
                    "type": "music_action_ack",
                    "action": str(action),
                    "action_id": str(action_id),
                }
            )
        except Exception as exc:
            logger.debug("Failed to emit music_action_ack: %s", exc)

    async def _emit_music_action_error(self, action: str, action_id: str, error: object) -> None:
        if not self.web_service:
            return
        try:
            await self.web_service.broadcast(
                {
                    "type": "music_action_error",
                    "action": str(action),
                    "action_id": str(action_id),
                    "error": str(error),
                }
            )
        except Exception as exc:
            logger.debug("Failed to emit music_action_error: %s", exc)

    def set_new_session_handler(self, handler: Callable[[], Awaitable[str | None]] | None) -> None:
        """Update handler for quick-answer initiated new-session requests."""
        self.new_session_handler = handler
        self.new_session_enabled = bool(handler is not None)
        self.tool_definitions = build_tool_definitions(
            self.timers_enabled,
            self.music_enabled,
            self.recorder_enabled,
            self.new_session_enabled,
        )

    def has_tool_capabilities(self) -> bool:
        """Whether any tool family is enabled for quick-answer routing."""
        return bool(self.tool_definitions)

    async def summarize_for_tts(
        self,
        full_response_text: str,
        *,
        target_words: int = 20,
        timeout_ms: int | None = None,
        user_question: str = "",
    ) -> str:
        """Return a concise spoken reply for TTS using the quick-answer endpoint.

        When user_question is provided the model is asked to directly answer the
        user without reproducing any generated content from the response.
        Returns empty string when a reply could not be produced.
        """
        source_text = str(full_response_text or "").strip()
        if not source_text:
            return ""

        target = max(1, int(target_words))
        effective_timeout_s = (
            max(0.2, float(timeout_ms) / 1000.0)
            if timeout_ms is not None
            else self.timeout_s
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        question = str(user_question or "").strip()
        if question:
            system_prompt = (
                "You produce a concise spoken reply for text-to-speech. "
                f"Write approximately {target} words as one or two natural sentences. "
                "Directly answer the user's question or summarise the key result in a natural, conversational tone. "
                "Use the assistant's response to inform your reply — include the key facts, outcome, or answer. "
                "Do not narrate what you are doing; just give the reply. "
                "No markdown, bullet points, or code."
            )
            user_prompt = (
                "User asked (oldest to newest turns):\n"
                f"{question}\n\n"
                "Assistant's full response:\n"
                f"{source_text}\n\n"
                f"Write a natural ~{target}-word spoken reply that answers the user based on the assistant's response."
            )
        else:
            system_prompt = (
                "You compress assistant responses for text-to-speech. "
                f"Return exactly {target} words as a single plain sentence. "
                "Preserve key intent and critical facts. "
                "Do not add markdown, lists, or code."
            )
            user_prompt = (
                "Create a spoken summary for this assistant response:\n\n"
                f"{source_text}"
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": max(24, target * 5),
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.llm_url,
                    json=payload,
                    headers=headers,
                    timeout=effective_timeout_s,
                )

            if response.status_code != 200:
                logger.warning(
                    "TTS summary request returned status %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                return ""

            response_data = response.json()
            choices = response_data.get("choices") if isinstance(response_data, dict) else None
            if not choices:
                logger.warning("TTS summary response missing choices")
                return ""

            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = str(message.get("content", "") or "").strip()
            content = sanitize_quick_answer_text(content)
            if not content:
                logger.warning("TTS summary response was empty")
                return ""

            normalized = _truncate_to_target_words(content, target)
            if not normalized:
                logger.warning("TTS summary response was not speakable")
                return ""

            logger.info(
                "← QUICK ANSWER: Generated TTS summary (%d/%d words)",
                len(_word_tokens(normalized)),
                target,
            )
            return normalized
        except httpx.TimeoutException:
            logger.warning("TTS summary request timed out after %.1fs", effective_timeout_s)
            return ""
        except Exception as exc:
            logger.error("TTS summary request failed: %s", exc)
            return ""

    def pop_last_tool_steps(self) -> list[dict[str, str]]:
        """Return and clear tool-call steps from the most recent quick-answer run."""
        steps = list(self._last_tool_steps)
        self._last_tool_steps.clear()
        return steps

    def pop_last_model_recommendation(self) -> dict[str, str] | None:
        """Return and clear the last model recommendation emitted by quick-answer."""
        recommendation = dict(self._last_model_recommendation) if self._last_model_recommendation else None
        self._last_model_recommendation = None
        return recommendation

    async def _sync_web_music_state(self, trace: dict[str, Any] | None = None) -> None:
        if not (self.web_service and self.music_router and self.music_router.manager):
            return
        manager = self.music_router.manager
        sync_start_ts = time.monotonic()
        trace_id = str((trace or {}).get("trace_id", "")).strip()
        voice_load_complete_ts = (trace or {}).get("voice_load_complete_ts")

        # Push transport immediately for responsiveness.
        # Queue fetch is expensive on large libraries and can saturate the shared
        # list-query path; only force queue snapshot for playlist-load traces.
        try:
            transport = await manager.get_ui_music_state()
            self.web_service.update_music_transport(**transport)
        except Exception as exc:
            logger.debug("Failed to push voice music transport: %s", exc)
            return

        if not trace_id:
            # Non-playlist actions (play/pause/next/volume/etc): do not force queue refresh.
            # The periodic publisher updates queue when metadata indicates a change.
            return

        async def _push_queue_async() -> None:
            try:
                queue = await manager.get_ui_playlist(limit=80, timeout=2.5)
                queue_ready_ts = time.monotonic()
                if trace_id:
                    logger.info(
                        "🧭 Voice playlist trace %s: queue snapshot ready in %.1fms (since sync start)",
                        trace_id,
                        (queue_ready_ts - sync_start_ts) * 1000,
                    )
                self.web_service.update_music_queue(
                    queue,
                    trace_id=trace_id,
                    voice_load_complete_ts=voice_load_complete_ts,
                    sync_start_ts=sync_start_ts,
                )
                if trace_id:
                    logger.info(
                        "🧭 Voice playlist trace %s: queued music_queue broadcast handoff in %.1fms (since sync start)",
                        trace_id,
                        (time.monotonic() - sync_start_ts) * 1000,
                    )
            except Exception as exc:
                logger.debug("Failed to push voice music queue: %s", exc)

        asyncio.create_task(_push_queue_async())

        playlists_cb = getattr(self.web_service, "_on_music_list_playlists", None)
        if playlists_cb is not None:
            try:
                playlists = await playlists_cb("voice")
                self.web_service.update_music_playlists(playlists)
            except Exception as playlists_exc:
                logger.debug("Failed to update web playlists via callback: %s", playlists_exc)
        else:
            try:
                playlists = await asyncio.wait_for(manager.list_playlists(), timeout=3.0)
                self.web_service.update_music_playlists(playlists)
            except Exception as playlists_exc:
                logger.debug("Failed to update web playlists: %s", playlists_exc)
        
    async def get_quick_answer(self, user_query: str, *, chat_history: list[dict] | None = None) -> tuple[bool, str]:
        """
        Try to get a quick answer from the LLM.
        
        Args:
            user_query: The user's transcript/question
            chat_history: Optional recent chat messages from the web service for context.
                          Up to the last 10 user/assistant turns are included, each
                          hard-truncated (user ≤300 chars, assistant ≤150 chars).
            
        Returns:
            Tuple of (should_use_upstream, response_text)
            - If should_use_upstream is True, response_text will be empty and gateway should be used
            - If should_use_upstream is False, response_text contains the quick answer
        """
        self._last_model_recommendation = None
        should_use_upstream, reason = classify_upstream_decision(user_query)
        if should_use_upstream:
            logger.info("← QUICK ANSWER: Heuristic escalation to upstream (%s)", reason)
            return True, ""

        try:
            current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            system_prompt = build_system_prompt(
                current_datetime, 
                False, False, False, False,
                openclaw_models_available=self.openclaw_models_available
            )

            history_msgs = build_history_messages(chat_history) if chat_history else []
            
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *history_msgs,
                    {"role": "user", "content": user_query},
                ],
                "temperature": 0.0,  # Deterministic for factual answers
                "max_tokens": 100,  # Keep responses brief
            }
            
            logger.info("→ QUICK ANSWER: Querying LLM for: '%s'", user_query)
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.llm_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_s,
                )
                
            if response.status_code != 200:
                logger.warning(
                    "Quick answer LLM returned status %d: %s",
                    response.status_code,
                    response.text[:200]
                )
                return True, ""  # Fall back to upstream
                
            response_data = response.json()
            
            # Extract the assistant's message
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                logger.warning("Quick answer LLM response missing 'choices' field")
                return True, ""
                
            message = response_data["choices"][0].get("message", {})
            content = message.get("content", "").strip()
            
            if not content:
                logger.warning("Quick answer LLM returned empty content")
                return True, ""
            
            # Check if LLM wants to escalate to upstream
            if content == "USE_UPSTREAM_AGENT" or content.startswith("USE_UPSTREAM_AGENT"):
                logger.info("← QUICK ANSWER: LLM escalated to upstream agent")
                return True, ""
            
            logger.info("← QUICK ANSWER: Got response (%d chars): %s", len(content), content[:100])
            return False, sanitize_quick_answer_text(content)
            
        except httpx.TimeoutException:
            logger.warning("Quick answer LLM request timed out after %.1fs", self.timeout_s)
            return True, ""  # Fall back to upstream
        except Exception as exc:
            logger.error("Quick answer LLM failed: %s", exc)
            return True, ""  # Fall back to upstream

    async def get_quick_answer_with_tools(self, user_query: str, *, chat_history: list[dict] | None = None) -> tuple[bool, str]:
        """
        Try to get a quick answer with tool calling support.
        
        This method first attempts deterministic fast-path parsing for obvious
        timer/alarm/music commands. If that fails, it falls back to the LLM with tool
        calling enabled.
        
        Args:
            user_query: The user's transcript/question
            chat_history: Optional recent chat messages from the web service for context.
                          Up to the last 10 user/assistant turns are included, each
                          hard-truncated (user ≤300 chars, assistant ≤150 chars).
            
        Returns:
            Tuple of (should_use_upstream, response_text)
            - If should_use_upstream is True, response_text will be empty and gateway should be used
            - If should_use_upstream is False, response_text contains the quick answer
        """
        self._last_model_recommendation = None
        should_use_upstream, reason = classify_upstream_decision(
            user_query,
            timers_enabled=self.timers_enabled,
            music_enabled=self.music_enabled,
            recorder_enabled=self.recorder_enabled,
            new_session_enabled=self.new_session_enabled,
        )
        if should_use_upstream:
            logger.info("← QUICK ANSWER: Heuristic escalation to upstream (%s)", reason)
            return True, ""
        if reason in ("timer_alarm_local", "music_local", "recorder_local", "new_session_local"):
            logger.info("← QUICK ANSWER: Keeping request local via heuristic (%s)", reason)

        if self.new_session_enabled and self.new_session_handler:
            new_session_intent = any(re.search(pattern, user_query.lower()) for pattern in NEW_SESSION_INTENT_PATTERNS)
            if new_session_intent:
                result = await self.new_session_handler()
                spoken = sanitize_quick_answer_text(result or "Started a new session.")
                logger.info("← QUICK ANSWER: New-session fast-path execution: %s", _preview(spoken))
                return False, spoken

        if self.recorder_enabled and self.recorder_tool:
            recorder_fast_result = await self.recorder_tool.try_handle_fast_path(user_query)
            if recorder_fast_result is not None:
                logger.info("← QUICK ANSWER: Recorder fast-path execution: %s", _preview(recorder_fast_result))
                return False, sanitize_quick_answer_text(recorder_fast_result)

        # Try music fast-path first if enabled
        if self.music_enabled and self.music_router:
            voice_music_action_id: str | None = None
            voice_playlist_name = ""
            fast_match = None
            try:
                fast_match = self.music_router.parser.parse(user_query)
            except Exception:
                fast_match = None
            if fast_match and fast_match[0] in ("load_playlist", "play_playlist"):
                voice_music_action_id = self._new_voice_music_action_id()
                voice_playlist_name = str((fast_match[1] or {}).get("name", "") or "").strip()
                await self._emit_music_action_pending(
                    "music_load_playlist",
                    voice_music_action_id,
                    name=voice_playlist_name or None,
                )

            music_result = await self.music_router.handle_request(user_query, use_fast_path=True)
            if music_result is not None:
                if voice_music_action_id:
                    if str(music_result).lower().startswith("error"):
                        await self._emit_music_action_error("music_load_playlist", voice_music_action_id, music_result)
                    else:
                        voice_load_complete_ts = time.monotonic()
                        logger.info(
                            "🧭 Voice playlist trace %s: voice load completed (fast-path)",
                            voice_music_action_id,
                        )
                        await self._emit_music_action_ack("music_load_playlist", voice_music_action_id)
                # Fire-and-forget: ack already sent, client starts polling via
                # requestMusicStateRetry; state sync runs in background so TTS
                # is returned to the user without waiting for queue fetch.
                sync_trace = None
                if voice_music_action_id and not str(music_result).lower().startswith("error"):
                    sync_trace = {
                        "trace_id": voice_music_action_id,
                        "voice_load_complete_ts": voice_load_complete_ts,
                    }
                asyncio.create_task(self._sync_web_music_state(sync_trace))
                logger.info("← QUICK ANSWER: Music fast-path execution: %s", _preview(music_result))
                return False, sanitize_quick_answer_text(music_result)
        
        # Try timer/alarm fast-path
        if self.timers_enabled and self.tool_router:
            fast_path_result = await self.tool_router.try_deterministic_parse(user_query)
            if fast_path_result is not None:
                logger.info("← QUICK ANSWER: Fast-path tool execution: %s", _preview(fast_path_result))
                return False, sanitize_quick_answer_text(fast_path_result)
        
        # If neither system is enabled, fall back to regular quick answer
        if not self.has_tool_capabilities():
            return await self.get_quick_answer(user_query, chat_history=chat_history)
        
        # Fast-path didn't match, try LLM with tool calling
        try:
            self._last_tool_steps.clear()
            current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            system_prompt = build_system_prompt(
                current_datetime,
                self.timers_enabled,
                self.music_enabled,
                self.recorder_enabled,
                self.new_session_enabled,
                openclaw_models_available=self.openclaw_models_available,
            )
            music_like_query = bool(self.music_enabled and self.music_router and self.music_router.is_music_related(user_query))
            # Force tool use only when the classified local intent is actually tool-eligible.
            # Date/time local queries should stay local but do not require a tool invocation.
            tool_eligible = (
                (reason == "music_local" and music_like_query)
                or (reason == "timer_alarm_local" and self.timers_enabled and self.tool_router)
                or (reason == "recorder_local" and self.recorder_enabled and self.recorder_tool)
                or (reason == "new_session_local" and self.new_session_enabled and self.new_session_handler)
            )
            tool_choice_value = "required" if tool_eligible else "auto"

            history_msgs = build_history_messages(chat_history) if chat_history else []

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *history_msgs,
                    {"role": "user", "content": user_query},
                ],
                "temperature": 0.0,
                "max_tokens": 150,
                "tools": self.tool_definitions,
                "tool_choice": tool_choice_value,
            }
            
            logger.info("→ QUICK ANSWER (with tools): Querying LLM for: '%s'", user_query)
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.llm_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_s,
                )
                
            if response.status_code != 200:
                logger.warning(
                    "Quick answer LLM returned status %d: %s",
                    response.status_code,
                    response.text[:200]
                )
                return True, ""  # Fall back to upstream
                
            response_data = response.json()
            
            if "choices" not in response_data or len(response_data["choices"]) == 0:
                logger.warning("Quick answer LLM response missing 'choices' field")
                return True, ""
                
            message = response_data["choices"][0].get("message", {})
            
            # Check if LLM made a tool call
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                # Execute the tool call(s)
                results = []
                for tool_call in tool_calls:
                    func_name = tool_call.get("function", {}).get("name")
                    func_args = tool_call.get("function", {}).get("arguments", "{}")

                    if isinstance(func_name, str):
                        normalized_name = func_name.strip()
                        if normalized_name.upper().startswith("USE_UPSTREAM_AGENT"):
                            logger.info("← QUICK ANSWER: LLM requested upstream escalation via tool name")
                            return True, ""
                        func_name = normalized_name
                    
                    if func_name:
                        logger.info("← QUICK ANSWER: LLM requested tool call: %s", func_name)
                        args_preview = _preview(func_args, 220)
                        self._last_tool_steps.append(
                            {
                                "name": str(func_name),
                                "phase": "start",
                                "details": f"args={args_preview}",
                            }
                        )
                        try:
                            import json
                            args_dict = json.loads(func_args) if isinstance(func_args, str) else func_args

                            # LLM may emit bare numeric alarm args (e.g., 5) and drop units from
                            # the function arguments even when user said "five seconds".
                            # Recover unit hints from the original transcript.
                            if func_name == "set_alarm" and isinstance(args_dict, dict):
                                raw_time = args_dict.get("time_str")
                                if raw_time in (None, ""):
                                    raw_time = args_dict.get("trigger_time")
                                lowered_query = user_query.lower()
                                inferred_unit = None
                                if re.search(r"\bsec(?:ond)?s?\b", lowered_query):
                                    inferred_unit = "second"
                                elif re.search(r"\bmin(?:ute)?s?\b", lowered_query):
                                    inferred_unit = "minute"
                                elif re.search(r"\bhour?s?\b", lowered_query):
                                    inferred_unit = "hour"

                                if inferred_unit is not None:
                                    args_dict["time_unit_hint"] = inferred_unit

                                _relative_unit_re = re.compile(
                                    r'^(\d+)\s*(sec(?:ond)?|min(?:ute)?|hour)s?$',
                                    re.IGNORECASE,
                                )
                                _rel_match = isinstance(raw_time, str) and _relative_unit_re.match(raw_time.strip())
                                if isinstance(raw_time, (int, float)) or (
                                    isinstance(raw_time, str) and raw_time.strip().isdigit()
                                ):
                                    amount = int(str(raw_time).strip())
                                    if amount > 0:
                                        if inferred_unit is not None:
                                            normalized = f"in {amount} {inferred_unit}{'s' if amount != 1 else ''}"
                                            args_dict["time_str"] = normalized
                                            logger.info(
                                                "↺ Normalized set_alarm numeric arg from transcript context: %s",
                                                normalized,
                                            )
                                elif _rel_match:
                                    # e.g. "10 seconds" — LLM passed relative expression without 'in' prefix
                                    amount = int(_rel_match.group(1))
                                    raw_unit = _rel_match.group(2).lower()
                                    # Normalise abbreviations to full unit name
                                    unit_map = {"sec": "second", "second": "second", "min": "minute", "minute": "minute", "hour": "hour"}
                                    unit_full = unit_map.get(raw_unit, raw_unit)
                                    if amount > 0:
                                        normalized = f"in {amount} {unit_full}{'s' if amount != 1 else ''}"
                                        args_dict["time_str"] = normalized
                                        logger.info(
                                            "↺ Normalized set_alarm relative-unit arg: %s",
                                            normalized,
                                        )
                            
                            # Route to appropriate handler
                            if func_name.startswith("music_") and self.music_enabled and self.music_router:
                                voice_music_action_id: str | None = None
                                voice_action_name: str | None = None
                                if func_name in ("music_load_playlist", "music_play_playlist"):
                                    voice_action_name = "music_load_playlist"
                                    voice_music_action_id = self._new_voice_music_action_id()
                                    await self._emit_music_action_pending(voice_action_name, voice_music_action_id)
                                elif func_name == "music_clear_queue":
                                    voice_action_name = "music_clear_queue"
                                    voice_music_action_id = self._new_voice_music_action_id()
                                    await self._emit_music_action_pending(voice_action_name, voice_music_action_id)

                                result = await self.music_router.handle_tool_call(func_name, args_dict)

                                if voice_music_action_id and voice_action_name:
                                    if str(result).lower().startswith("error"):
                                        await self._emit_music_action_error(voice_action_name, voice_music_action_id, result)
                                    else:
                                        voice_load_complete_ts = time.monotonic()
                                        logger.info(
                                            "🧭 Voice music trace %s: action completed (tool-call %s)",
                                            voice_music_action_id,
                                            func_name,
                                        )
                                        await self._emit_music_action_ack(voice_action_name, voice_music_action_id)
                                # Fire-and-forget (same rationale as fast-path above).
                                sync_trace = None
                                if (
                                    voice_music_action_id
                                    and voice_action_name == "music_load_playlist"
                                    and not str(result).lower().startswith("error")
                                ):
                                    sync_trace = {
                                        "trace_id": voice_music_action_id,
                                        "voice_load_complete_ts": voice_load_complete_ts,
                                    }
                                asyncio.create_task(self._sync_web_music_state(sync_trace))
                            elif func_name == "recorder" and self.recorder_enabled and self.recorder_tool:
                                result = await self.recorder_tool.execute_tool(**args_dict)
                            elif func_name == "start_new_session" and self.new_session_enabled and self.new_session_handler:
                                result = await self.new_session_handler()
                            elif self.timers_enabled and self.tool_router:
                                result = await self.tool_router.execute_tool(func_name, args_dict)
                            else:
                                result = f"Tool handler not available for {func_name}"
                            
                            spoken_result = sanitize_quick_answer_text(result)
                            results.append(spoken_result)
                            self._last_tool_steps.append(
                                {
                                    "name": str(func_name),
                                    "phase": "end",
                                    "details": f"result={_preview(spoken_result, 220)}",
                                }
                            )
                        except Exception as e:
                            logger.error("Tool execution failed for %s: %s", func_name, e)
                            results.append(f"Error: {str(e)}")
                            self._last_tool_steps.append(
                                {
                                    "name": str(func_name),
                                    "phase": "end",
                                    "details": f"error={_preview(e, 220)}",
                                }
                            )
                
                # Return the tool execution result(s)
                final_result = " ".join(results) if results else "Tool execution completed"
                return False, sanitize_quick_answer_text(final_result)
            
            # No tool calls, check for regular content response
            content = message.get("content", "").strip()

            if music_like_query and not tool_calls:
                # Media command policy: never speak long free-form LLM prose for music control.
                # If model failed to emit a tool call, escalate so gateway/tooling can decide,
                # rather than returning verbose text.
                logger.info("← QUICK ANSWER: Music-like query returned no tool call; escalating upstream")
                return True, ""
            
            if not content:
                logger.warning("Quick answer LLM returned empty content with no tool calls")
                return True, ""
            
            # Check if LLM wants to escalate to upstream
            if content == "USE_UPSTREAM_AGENT" or content.startswith("USE_UPSTREAM_AGENT"):
                logger.info("← QUICK ANSWER: LLM escalated to upstream agent")
                return True, ""

            if reason == "date_time_local":
                logger.info("← QUICK ANSWER: Returning local date/time response from tool-enabled path")
                return False, sanitize_quick_answer_text(content)
            
            # Strict two-outcome contract enforcement: response must be either
            # (1) tool calls (already handled above)
            # (2) model_recommendation JSON (only if OpenClaw models are available)
            # (3) USE_UPSTREAM_AGENT
            # Anything else (free-form prose) is a contract violation
            
            try:
                # Try to parse as JSON for model_recommendation (if models are available)
                import json
                parsed = json.loads(content)
                if isinstance(parsed, dict) and parsed.get("type") == "model_recommendation":
                    # Check if models are actually available before accepting recommendation
                    if not self.openclaw_models_available:
                        logger.warning(
                            "← QUICK ANSWER: LLM returned model_recommendation but no OpenClaw models available; escalating upstream"
                        )
                        return True, ""
                    
                    tier = parsed.get("tier")
                    recommendation_reason = parsed.get("reason", "quick-answer model recommendation")
                    if isinstance(tier, str) and tier in ("fast", "basic", "capable", "smart", "genius"):
                        logger.info(
                            "← QUICK ANSWER: Model recommendation returned (tier=%s, reason=%s)",
                            tier,
                            recommendation_reason,
                        )
                        self._last_model_recommendation = {
                            "type": "model_recommendation",
                            "tier": tier,
                            "reason": str(recommendation_reason),
                        }
                        return True, ""
                    else:
                        logger.warning("← QUICK ANSWER: Invalid tier in model_recommendation: %s", tier)
                        return True, ""
            except (json.JSONDecodeError, ValueError):
                pass  # Not JSON, continue to prose check
            
            # If we get here, it's free-form prose - violates strict two-outcome contract
            # Escalate to upstream rather than returning unstructured content to TTS
            logger.warning(
                "← QUICK ANSWER: LLM returned free-form prose (violates two-outcome contract); escalating upstream: %s",
                content[:100],
            )
            return True, ""
            
        except httpx.TimeoutException:
            logger.warning("Quick answer LLM request timed out after %.1fs", self.timeout_s)
            return True, ""
        except Exception as exc:
            logger.error("Quick answer LLM with tools failed: %s", exc)
            return True, ""
