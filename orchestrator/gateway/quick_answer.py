"""Quick answer LLM client for fast factual responses."""
import logging
import re
import httpx
import random
from datetime import datetime
from typing import Awaitable, Callable, Optional


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


QUICK_ANSWER_BASE_SYSTEM_PROMPT = """You are a strict validation gatekeeper. Your sole objective is to provide immediate answers only when they are factual, indisputable, and concise.

Strict Response Protocol:
- Verification Requirement: Before answering, mentally verify the fact against your training or tools. If the information is subject to change, opinion-based, or requires nuance, you must fail the check.
- The "Uncertainty" Trigger: If there is even a 1% margin of doubt, or if the query involves complex reasoning, reply exactly with: USE_UPSTREAM_AGENT.
- Constraint: Answers must be exactly one to two sentences. No conversational filler, no "I believe," no "As of my last update," and no sign-off phrases such as "is there anything else I can help with" or "let me know if you need more."
- Binary Outcome: Your output is either a short, definitive fact or the escalation code. Any middle ground is a failure of your instructions.
- If the user is asking about personal data, account-specific state, email, inbox contents, notifications, messages, calendar items, or anything that depends on prior conversation context or external state, you must reply exactly with: USE_UPSTREAM_AGENT.
- If the user references earlier dialogue with phrases like "you never told me", "what about", "did I get", "any new ones", "check my", or "do I have", you must reply exactly with: USE_UPSTREAM_AGENT unless a timer/alarm/music tool directly answers it.

Current date and time: {current_datetime}

{tool_usage_section}"""


def build_tool_usage_section(timers_enabled: bool, music_enabled: bool, recorder_enabled: bool, new_session_enabled: bool) -> str:
    """Build a prompt section that only mentions tool families that are actually available."""
    sections: list[str] = []

    if timers_enabled or music_enabled or recorder_enabled or new_session_enabled:
        sections.append("Tool Usage:")
        if timers_enabled and music_enabled and recorder_enabled:
            sections.append("- When user requests timer, alarm, music control, or recording operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm/music/recording requests - do not escalate these to upstream agent.")
        elif timers_enabled and music_enabled:
            sections.append("- When user requests timer, alarm, or music control operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm/music requests - do not escalate these to upstream agent.")
        elif timers_enabled and recorder_enabled:
            sections.append("- When user requests timer, alarm, or recording operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm/recording requests - do not escalate these to upstream agent.")
        elif music_enabled and recorder_enabled:
            sections.append("- When user requests music control or recording operations, use the provided tools.")
            sections.append("- Only use tools for music/recording requests - do not escalate these to upstream agent.")
        elif timers_enabled:
            sections.append("- When user requests timer or alarm operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm requests - do not escalate these to upstream agent.")
        elif music_enabled:
            sections.append("- When user requests music control operations, use the provided tools.")
            sections.append("- Only use tools for music control requests - do not escalate these to upstream agent.")
        elif recorder_enabled:
            sections.append("- When user requests recording operations, use the provided tools.")
            sections.append("- Only use tools for recording requests - do not escalate these to upstream agent.")
        if new_session_enabled:
            sections.append("- When user requests starting a new chat/session/conversation, use the provided session tool.")
            sections.append("- Use the session tool for requests like 'start a new session' or 'new chat'.")

    sections.append("- For all other uncertain queries, use USE_UPSTREAM_AGENT")
    return "\n".join(sections)


def build_system_prompt(
    current_datetime: str,
    timers_enabled: bool,
    music_enabled: bool,
    recorder_enabled: bool,
    new_session_enabled: bool,
) -> str:
    """Build the system prompt for the current tool capabilities."""
    return QUICK_ANSWER_BASE_SYSTEM_PROMPT.format(
        current_datetime=current_datetime,
        tool_usage_section=build_tool_usage_section(timers_enabled, music_enabled, recorder_enabled, new_session_enabled),
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

# Action/task intents should go upstream so tools/agents can execute the request.
ACTION_INTENT_PATTERNS = [
    r"\b(add|append|put|include)\b.*\b(shopping list|grocery list|todo list|to-do list|list)\b",
    r"\b(shopping|grocery)\b.*\b(add|buy|get|pick up)\b",
    r"^\s*(also\s+)?add\b",
    r"\b(remind me|set up|create|book|schedule|order|send|message|email|call)\b",
    r"\b(open|launch|start)\b.*\b(browser|web\s*browser|tab|window)\b",
    r"\b(open|go to|navigate to|visit)\b\s+([\w-]+\.)+[a-z]{2,}\b",
]

RECORDER_INTENT_PATTERNS = [
    r"\b(start|begin)\s+(the\s+)?record(ing)?\b",
    r"\b(stop|end|finish)\s+(the\s+)?record(ing)?\b",
    r"\b(recorder\s+status|recording\s+status)\b",
    r"\b(recorder\s+on|recorder\s+off)\b",
]

NEW_SESSION_INTENT_PATTERNS = [
    r"\b(start|create|open|begin)\b.*\b(new|fresh)\b.*\b(session|chat|conversation)\b",
    r"\b(new|fresh)\b.*\b(session|chat|conversation)\b",
    r"\b(reset|clear)\b.*\b(session|chat|conversation)\b",
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

    if any(re.search(pattern, query) for pattern in TIME_SENSITIVE_PATTERNS):
        return True, "time_sensitive"

    # If timers/alarms or music tooling is available, keep those intents local.
    if timers_enabled and any(re.search(pattern, query) for pattern in TIMER_ALARM_INTENT_PATTERNS):
        return False, "timer_alarm_local"

    if music_enabled and any(re.search(pattern, query) for pattern in MUSIC_INTENT_PATTERNS):
        return False, "music_local"

    recorder_intent = any(re.search(pattern, query) for pattern in RECORDER_INTENT_PATTERNS)
    if recorder_enabled and recorder_intent:
        return False, "recorder_local"
    if recorder_intent:
        return True, "recording_action_disabled"

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
            "description": "Play music from a specific genre",
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

    def pop_last_tool_steps(self) -> list[dict[str, str]]:
        """Return and clear tool-call steps from the most recent quick-answer run."""
        steps = list(self._last_tool_steps)
        self._last_tool_steps.clear()
        return steps

    async def _sync_web_music_state(self) -> None:
        if not (self.web_service and self.music_router and self.music_router.manager):
            return
        try:
            transport = await self.music_router.manager.get_ui_music_state()
            queue = await self.music_router.manager.get_ui_playlist()
            self.web_service.update_music_state(queue=queue, **transport)
        except Exception as update_exc:
            logger.debug("Failed to update web music state: %s", update_exc)
        
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
        should_use_upstream, reason = classify_upstream_decision(user_query)
        if should_use_upstream:
            logger.info("← QUICK ANSWER: Heuristic escalation to upstream (%s)", reason)
            return True, ""

        try:
            current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            system_prompt = build_system_prompt(current_datetime, False, False, False, False)

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
            music_result = await self.music_router.handle_request(user_query, use_fast_path=True)
            if music_result is not None:
                await self._sync_web_music_state()
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
            )
            music_like_query = bool(self.music_enabled and self.music_router and self.music_router.is_music_related(user_query))

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
                "tool_choice": "required" if music_like_query else "auto",
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
                                result = await self.music_router.handle_tool_call(func_name, args_dict)
                                await self._sync_web_music_state()
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
            return True, ""
        except Exception as exc:
            logger.error("Quick answer LLM with tools failed: %s", exc)
            return True, ""
