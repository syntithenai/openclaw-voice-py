"""Quick answer LLM client for fast factual responses."""
import logging
import re
import httpx
import random
from datetime import datetime
from typing import Optional


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

    def sanitize_quick_answer_text(text: str) -> str:
        """Normalize quick-answer text for speech-friendly playback.

        The quick-answer path can emit markdown emphasis markers (e.g. *italic*, **bold**),
        which are sometimes spoken literally by TTS engines. Strip those markers while
        preserving the underlying words.
        """
        if not isinstance(text, str):
            return ""

        cleaned = text.replace("**", "").replace("*", "")
        return re.sub(r"\s+", " ", cleaned).strip()


QUICK_ANSWER_BASE_SYSTEM_PROMPT = """You are a strict validation gatekeeper. Your sole objective is to provide immediate answers only when they are factual, indisputable, and concise.

Strict Response Protocol:
- Verification Requirement: Before answering, mentally verify the fact against your training or tools. If the information is subject to change, opinion-based, or requires nuance, you must fail the check.
- The "Uncertainty" Trigger: If there is even a 1% margin of doubt, or if the query involves complex reasoning, reply exactly with: USE_UPSTREAM_AGENT.
- Constraint: Answers must be exactly one to two sentences. No conversational filler, no "I believe," and no "As of my last update."
- Binary Outcome: Your output is either a short, definitive fact or the escalation code. Any middle ground is a failure of your instructions.
- If the user is asking about personal data, account-specific state, email, inbox contents, notifications, messages, calendar items, or anything that depends on prior conversation context or external state, you must reply exactly with: USE_UPSTREAM_AGENT.
- If the user references earlier dialogue with phrases like "you never told me", "what about", "did I get", "any new ones", "check my", or "do I have", you must reply exactly with: USE_UPSTREAM_AGENT unless a timer/alarm/music tool directly answers it.

Current date and time: {current_datetime}

{tool_usage_section}"""


def build_tool_usage_section(timers_enabled: bool, music_enabled: bool) -> str:
    """Build a prompt section that only mentions tool families that are actually available."""
    sections: list[str] = []

    if timers_enabled or music_enabled:
        sections.append("Tool Usage:")
        if timers_enabled and music_enabled:
            sections.append("- When user requests timer, alarm, or music control operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm/music requests - do not escalate these to upstream agent.")
        elif timers_enabled:
            sections.append("- When user requests timer or alarm operations, use the provided tools.")
            sections.append("- Only use tools for timer/alarm requests - do not escalate these to upstream agent.")
        elif music_enabled:
            sections.append("- When user requests music control operations, use the provided tools.")
            sections.append("- Only use tools for music control requests - do not escalate these to upstream agent.")

    sections.append("- For all other uncertain queries, use USE_UPSTREAM_AGENT")
    return "\n".join(sections)


def build_system_prompt(current_datetime: str, timers_enabled: bool, music_enabled: bool) -> str:
    """Build the system prompt for the current tool capabilities."""
    return QUICK_ANSWER_BASE_SYSTEM_PROMPT.format(
        current_datetime=current_datetime,
        tool_usage_section=build_tool_usage_section(timers_enabled, music_enabled),
    )


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
]


def should_force_upstream(user_query: str) -> bool:
    """Return True for queries that should bypass quick answer and go upstream."""
    query = user_query.strip().lower()
    if not query:
        return True

    if any(re.search(pattern, query) for pattern in UPSTREAM_ONLY_PATTERNS):
        return True

    if any(re.search(pattern, query) for pattern in WEB_LOOKUP_PATTERNS):
        return True

    if any(re.search(pattern, query) for pattern in TIME_SENSITIVE_PATTERNS):
        return True

    if any(re.search(pattern, query) for pattern in ACTION_INTENT_PATTERNS):
        return True

    return False


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
                        "description": "Time in format like '6:30 AM', '18:30', 'tomorrow 9am'"
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


def build_tool_definitions(timers_enabled: bool, music_enabled: bool) -> list[dict]:
    """Return only the tool definitions that are actually available."""
    tool_definitions: list[dict] = []
    if timers_enabled:
        tool_definitions.extend(TIMER_TOOL_DEFINITIONS)
    if music_enabled:
        tool_definitions.extend(MUSIC_TOOL_DEFINITIONS)
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
        tool_router = None,
        music_router = None,
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
        """
        self.llm_url = llm_url
        self.model = model or "gpt-3.5-turbo"  # Default fallback
        self.api_key = api_key
        self.timeout_s = timeout_ms / 1000.0
        self.tool_router = tool_router
        self.music_router = music_router
        self.timers_enabled = bool(timers_enabled and tool_router is not None)
        self.music_enabled = bool(music_enabled and music_router is not None)
        self.tool_definitions = build_tool_definitions(self.timers_enabled, self.music_enabled)

    def has_tool_capabilities(self) -> bool:
        """Whether any tool family is enabled for quick-answer routing."""
        return bool(self.tool_definitions)
        
    async def get_quick_answer(self, user_query: str) -> tuple[bool, str]:
        """
        Try to get a quick answer from the LLM.
        
        Args:
            user_query: The user's transcript/question
            
        Returns:
            Tuple of (should_use_upstream, response_text)
            - If should_use_upstream is True, response_text will be empty and gateway should be used
            - If should_use_upstream is False, response_text contains the quick answer
        """
        if should_force_upstream(user_query):
            logger.info("← QUICK ANSWER: Heuristic escalation to upstream for context/account-specific query")
            return True, ""

        try:
            current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            system_prompt = build_system_prompt(current_datetime, False, False)
            
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
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

    async def get_quick_answer_with_tools(self, user_query: str) -> tuple[bool, str]:
        """
        Try to get a quick answer with tool calling support.
        
        This method first attempts deterministic fast-path parsing for obvious
        timer/alarm/music commands. If that fails, it falls back to the LLM with tool
        calling enabled.
        
        Args:
            user_query: The user's transcript/question
            
        Returns:
            Tuple of (should_use_upstream, response_text)
            - If should_use_upstream is True, response_text will be empty and gateway should be used
            - If should_use_upstream is False, response_text contains the quick answer
        """
        if should_force_upstream(user_query):
            logger.info("← QUICK ANSWER: Heuristic escalation to upstream for context/account-specific query")
            return True, ""

        # Try music fast-path first if enabled
        if self.music_enabled and self.music_router:
            music_result = await self.music_router.handle_request(user_query, use_fast_path=True)
            if music_result is not None:
                logger.info("← QUICK ANSWER: Music fast-path execution: %s", music_result[:100])
                return False, sanitize_quick_answer_text(music_result)
        
        # Try timer/alarm fast-path
        if self.timers_enabled and self.tool_router:
            fast_path_result = await self.tool_router.try_deterministic_parse(user_query)
            if fast_path_result is not None:
                logger.info("← QUICK ANSWER: Fast-path tool execution: %s", fast_path_result[:100])
                return False, sanitize_quick_answer_text(fast_path_result)
        
        # If neither system is enabled, fall back to regular quick answer
        if not self.has_tool_capabilities():
            return await self.get_quick_answer(user_query)
        
        # Fast-path didn't match, try LLM with tool calling
        try:
            current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            system_prompt = build_system_prompt(current_datetime, self.timers_enabled, self.music_enabled)
            
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
                ],
                "temperature": 0.0,
                "max_tokens": 150,
                "tools": self.tool_definitions,
                "tool_choice": "auto",
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
                        try:
                            import json
                            args_dict = json.loads(func_args) if isinstance(func_args, str) else func_args
                            
                            # Route to appropriate handler
                            if func_name.startswith("music_") and self.music_enabled and self.music_router:
                                result = await self.music_router.handle_tool_call(func_name, args_dict)
                            elif self.timers_enabled and self.tool_router:
                                result = await self.tool_router.execute_tool(func_name, args_dict)
                            else:
                                result = f"Tool handler not available for {func_name}"
                            
                            results.append(result)
                        except Exception as e:
                            logger.error("Tool execution failed for %s: %s", func_name, e)
                            results.append(f"Error: {str(e)}")
                
                # Return the tool execution result(s)
                final_result = " ".join(results) if results else "Tool execution completed"
                return False, sanitize_quick_answer_text(final_result)
            
            # No tool calls, check for regular content response
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
            return True, ""
        except Exception as exc:
            logger.error("Quick answer LLM with tools failed: %s", exc)
            return True, ""
