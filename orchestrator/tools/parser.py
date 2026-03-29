"""Deterministic fast-path parsing for timer/alarm commands and time expression parsing."""

import re
import time
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("orchestrator.tools.parser")


class FastPathParser:
    """Parse common timer/alarm commands without LLM for low latency."""
    
    # Timer setting patterns
    TIMER_PATTERNS = [
        # "set timer 5 minutes"
        (r'set\s+(?:a\s+)?timer\s+(?:for\s+)?(\d+)\s*(min|minute|sec|second|hour)s?', 'set_timer'),
        # "set a 10 minute timer"
        (r'set\s+(?:a|an)\s+(\d+)\s*(min|minute|sec|second|hour)s?\s+timer', 'set_timer'),
        # "timer for 30 seconds"
        (r'timer\s+for\s+(\d+)\s*(min|minute|sec|second|hour)s?', 'set_timer'),
    ]

    # Relative alarm setting patterns
    ALARM_SET_PATTERNS = [
        # "set an alarm for 10 seconds", "set alarm in 2 hours"
        (r'set\s+(?:an?\s+)?alarm\s+(?:for|in)\s+(\d+)\s*(min|minute|sec|second|hour)s?', 'set_alarm_relative'),
        # "alarm for 30 seconds"
        (r'alarm\s+(?:for|in)\s+(\d+)\s*(min|minute|sec|second|hour)s?', 'set_alarm_relative'),
    ]
    
    # Timer query patterns
    TIMER_QUERY_PATTERNS = [
        (r'how\s+much\s+time', 'list_timers'),
        (r'time\s+left', 'list_timers'),
        (r'time\s+remaining', 'list_timers'),
        (r'what\s+timers', 'list_timers'),
        (r'list\s+timers', 'list_timers'),
    ]
    
    # Timer cancel patterns
    TIMER_CANCEL_PATTERNS = [
        # Generic cancel (no label) — place before label extraction so
        # "cancel the timer" doesn't capture "the" as a label.
        (r'cancel\s+(?:(?:all|the)\s+)?timers?$', 'cancel_all_timers'),
        # Label-based cancel: "cancel kitchen timer", "cancel the pasta timer"
        (r'cancel\s+(?:the\s+)?(?!the\b|a\b|an\b|all\b)(\w+)\s+timer\b', 'cancel_timer_by_label'),
        (r'cancel\s+(?:all\s+)?timers?', 'cancel_all_timers'),
        (r'stop\s+(?:the\s+)?timer', 'cancel_all_timers'),
    ]
    
    # Alarm stop patterns
    ALARM_STOP_PATTERNS = [
        # Generic stop (no label) — must come before the label-extraction pattern so
        # articles like "the" are not accidentally captured as a label.
        # Handles: "stop alarm", "stop alarms", "stop all alarms", "stop the alarm"
        (r'stop\s+(?:(?:all|the)\s+)?alarms?$', 'stop_alarm'),
        # Label-based stop: "stop the morning alarm", "stop the oven alarm"
        (r'stop\s+(?:the\s+)?(\w+)\s+alarm', 'stop_alarm_by_label'),
        (r'dismiss\s+(?:the\s+)?alarms?', 'stop_alarm'),
        (r'turn\s+off\s+(?:the\s+)?alarms?', 'stop_alarm'),
    ]
    
    TIME_MULTIPLIERS = {
        'sec': 1,
        'second': 1,
        'min': 60,
        'minute': 60,
        'hour': 3600,
    }
    
    def __init__(self):
        self.compiled_patterns = []
        for pattern, action in (self.TIMER_PATTERNS +
                       self.ALARM_SET_PATTERNS +
                               self.TIMER_QUERY_PATTERNS +
                               self.TIMER_CANCEL_PATTERNS +
                               self.ALARM_STOP_PATTERNS):
            self.compiled_patterns.append((re.compile(pattern, re.IGNORECASE), action))
    
    def parse(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Try to parse a timer/alarm command using fast-path patterns.
        
        Args:
            text: User transcript
            
        Returns:
            Tuple of (action, arguments) if matched, None otherwise
        """
        text = text.strip().lower().rstrip('.!?,;:')
        
        for pattern, action in self.compiled_patterns:
            match = pattern.search(text)
            if match:
                return self._extract_action(action, match, text)
        
        return None
    
    def _extract_action(self, action: str, match: re.Match, text: str) -> Tuple[str, Dict[str, Any]]:
        """Extract action and arguments from regex match."""
        
        if action == 'set_timer':
            duration = int(match.group(1))
            unit = match.group(2).lower()
            multiplier = self.TIME_MULTIPLIERS.get(unit, 60)
            duration_seconds = duration * multiplier
            
            # Try to extract label from remaining text
            label = self._extract_timer_label(text, match)
            
            return ('set_timer', {
                'duration_seconds': duration_seconds,
                'label': label
            })

        elif action == 'set_alarm_relative':
            amount = int(match.group(1))
            unit_raw = match.group(2).lower()
            if unit_raw.startswith('sec'):
                unit = 'second'
            elif unit_raw.startswith('hour'):
                unit = 'hour'
            else:
                unit = 'minute'

            trigger_time = f"in {amount} {unit}{'s' if amount != 1 else ''}"
            return ('set_alarm', {
                'trigger_time': trigger_time,
                'label': '',
            })
        
        elif action == 'list_timers':
            return ('list_timers', {})
        
        elif action == 'cancel_timer_by_label':
            label = match.group(1)
            return ('cancel_timer', {'label': label})
        
        elif action == 'cancel_all_timers':
            return ('cancel_all_timers', {})
        
        elif action == 'stop_alarm':
            return ('stop_alarm', {'alarm_id': None})
        
        elif action == 'stop_alarm_by_label':
            label = match.group(1)
            return ('stop_alarm', {'label': label})
        
        return (action, {})
    
    def _extract_timer_label(self, text: str, match: re.Match) -> str:
        """Try to extract a timer label from the command."""
        # Look for "for X" or "called X" or "named X" after the duration
        label_patterns = [
            r'(?:for|called|named)\s+(?:the\s+)?(\w+)',
            r'(\w+)\s+timer',
        ]
        
        # Search in text after the match
        search_start = match.end()
        remaining_text = text[search_start:]
        
        for pattern in label_patterns:
            label_match = re.search(pattern, remaining_text)
            if label_match:
                label = label_match.group(1)
                # Filter out common words
                if label not in ['timer', 'alarm', 'the', 'a', 'an', 'for']:
                    return label
        
        return ""


class TimeExpressionParser:
    """Parse natural time expressions into timestamps."""
    
    def parse_alarm_time(self, expression: str) -> Optional[float]:
        """
        Parse alarm time expression.
        
        Args:
            expression: Time expression like "6:30 AM", "in 2 hours", "18:30"
            
        Returns:
            Unix timestamp (float) or None if parsing failed
        """
        expression = expression.strip().lower()
        
        # Try absolute time formats
        timestamp = self._parse_absolute_time(expression)
        if timestamp:
            return timestamp
        
        # Try relative time formats
        timestamp = self._parse_relative_time(expression)
        if timestamp:
            return timestamp
        
        return None
    
    def _parse_absolute_time(self, expr: str) -> Optional[float]:
        """Parse absolute time expressions like '6:30 AM' or '18:30'."""
        
        # Try 12-hour format with AM/PM
        patterns_12h = [
            r'(\d{1,2}):(\d{2})\s*(am|pm)',
            r'(\d{1,2})\s*(am|pm)',
        ]
        
        for pattern in patterns_12h:
            match = re.search(pattern, expr, re.IGNORECASE)
            if match:
                try:
                    hour = int(match.group(1))
                    minute = int(match.group(2)) if len(match.groups()) >= 2 and match.group(2) else 0
                    period = match.group(-1).lower()
                    
                    # Convert to 24-hour
                    if period == 'pm' and hour != 12:
                        hour += 12
                    elif period == 'am' and hour == 12:
                        hour = 0
                    
                    return self._time_to_timestamp(hour, minute)
                except ValueError:
                    continue
        
        # Try 24-hour format
        match = re.match(r'(\d{1,2}):(\d{2})', expr)
        if match:
            try:
                hour = int(match.group(1))
                minute = int(match.group(2))
                return self._time_to_timestamp(hour, minute)
            except ValueError:
                pass
        
        return None
    
    def _parse_relative_time(self, expr: str) -> Optional[float]:
        """Parse relative time expressions like 'in 2 hours', '10 seconds', 'for 5 minutes'."""

        # Optional leading 'in'/'for', e.g. "in 2 hours", "10 seconds", "for 5 minutes"
        match = re.search(r'(?:(?:in|for)\s+)?(\d+)\s*(min|minute|hour|sec|second)s?(?:\b|$)', expr)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)

            multiplier = FastPathParser.TIME_MULTIPLIERS.get(unit, 60)
            delta_seconds = amount * multiplier

            return time.time() + delta_seconds

        return None
    
    def _time_to_timestamp(self, hour: int, minute: int) -> float:
        """Convert time to next occurrence timestamp."""
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If time has passed today, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)
        
        return target.timestamp()


# Global instances
fast_path_parser = FastPathParser()
time_parser = TimeExpressionParser()
