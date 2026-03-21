"""Timer implementation with file-based persistence."""

import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional, Callable, List
from .uuid_utils import generate_uuidv7
from .state import StateManager

logger = logging.getLogger("orchestrator.tools.timer")


@dataclass
class Timer:
    """Timer data structure."""
    id: str
    duration_seconds: int
    created_at: float
    expires_at: float
    label: str = ""
    cancelled: bool = False
    completed: bool = False
    callback: Optional[Callable] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence (excluding callback)."""
        return {
            'type': 'timer',
            'id': self.id,
            'duration_seconds': self.duration_seconds,
            'created_at': self.created_at,
            'expires_at': self.expires_at,
            'label': self.label,
            'cancelled': self.cancelled,
            'completed': self.completed,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Timer':
        """Create Timer from dictionary."""
        return cls(
            id=data['id'],
            duration_seconds=data['duration_seconds'],
            created_at=data['created_at'],
            expires_at=data['expires_at'],
            label=data.get('label', ''),
            cancelled=data.get('cancelled', False),
            completed=data.get('completed', False),
        )
    
    def time_remaining(self) -> float:
        """Get remaining time in seconds."""
        return max(0, self.expires_at - time.time())
    
    def is_expired(self) -> bool:
        """Check if timer has expired."""
        return time.time() >= self.expires_at

    def to_ui_dict(self, now_ts: float | None = None) -> dict:
        """Return lightweight dict for web UI serialization."""
        ts = now_ts if now_ts is not None else time.time()
        return {
            "id": self.id,
            "label": self.label,
            "remaining_seconds": max(0.0, self.expires_at - ts),
            "expires_at": self.expires_at,
            "duration_seconds": self.duration_seconds,
        }


class TimerManager:
    """Manages active timers."""
    
    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.active_timers: dict[str, Timer] = {}
    
    async def set_timer(
        self,
        duration_seconds: int,
        label: str = "",
        callback: Optional[Callable] = None
    ) -> str:
        """
        Create a new timer.
        
        Args:
            duration_seconds: Timer duration in seconds
            label: Optional label/name for the timer
            callback: Optional callback to invoke on expiration
            
        Returns:
            Timer ID
        """
        timer_id = generate_uuidv7()
        now = time.time()
        
        timer = Timer(
            id=timer_id,
            duration_seconds=duration_seconds,
            created_at=now,
            expires_at=now + duration_seconds,
            label=label,
            callback=callback
        )
        
        self.active_timers[timer_id] = timer
        
        # Persist to disk
        await self.state_manager.write_timer(timer_id, timer.to_dict(), critical=True)
        
        logger.info(
            f"Timer: Created timer {timer_id} ({label or 'unlabeled'}) "
            f"for {duration_seconds}s, expires at {timer.expires_at}"
        )
        
        return timer_id
    
    async def cancel_timer(self, timer_id: str) -> bool:
        """
        Cancel a timer by ID.
        
        Args:
            timer_id: Timer identifier
            
        Returns:
            True if cancelled, False if not found
        """
        timer = self.active_timers.get(timer_id)
        if not timer:
            logger.warning(f"Timer: Cannot cancel timer {timer_id} - not found")
            return False
        
        timer.cancelled = True
        self.active_timers.pop(timer_id, None)
        
        # Delete from disk
        await self.state_manager.delete_timer(timer_id)
        await self.state_manager.log_event('timer_cancelled', {'timer_id': timer_id, 'label': timer.label})
        
        logger.info(f"Timer: Cancelled timer {timer_id} ({timer.label})")
        return True
    
    async def cancel_timer_by_label(self, label: str) -> int:
        """
        Cancel timers by label.
        
        Args:
            label: Timer label
            
        Returns:
            Number of timers cancelled
        """
        cancelled_count = 0
        timers_to_cancel = [
            timer_id for timer_id, timer in self.active_timers.items()
            if timer.label.lower() == label.lower()
        ]
        
        for timer_id in timers_to_cancel:
            if await self.cancel_timer(timer_id):
                cancelled_count += 1
        
        return cancelled_count
    
    async def cancel_all_timers(self) -> int:
        """
        Cancel all active timers.
        
        Returns:
            Number of timers cancelled
        """
        timer_ids = list(self.active_timers.keys())
        cancelled_count = 0
        
        for timer_id in timer_ids:
            if await self.cancel_timer(timer_id):
                cancelled_count += 1
        
        return cancelled_count
    
    async def complete_timer(self, timer_id: str):
        """
        Mark timer as completed and delete.
        
        Args:
            timer_id: Timer identifier
        """
        timer = self.active_timers.get(timer_id)
        if not timer:
            return
        
        timer.completed = True
        self.active_timers.pop(timer_id, None)
        
        # Delete from disk
        await self.state_manager.delete_timer(timer_id)
        await self.state_manager.log_event('timer_completed', {
            'timer_id': timer_id,
            'label': timer.label,
            'duration_seconds': timer.duration_seconds
        })
        
        logger.info(f"Timer: Completed timer {timer_id} ({timer.label})")
    
    def get_timer(self, timer_id: str) -> Optional[Timer]:
        """Get timer by ID."""
        return self.active_timers.get(timer_id)
    
    def list_active_timers(self) -> List[Timer]:
        """Get list of all active timers."""
        return list(self.active_timers.values())

    def list_ui_timers(self, now_ts: float | None = None) -> list:
        """Return active timers as UI-ready dicts."""
        now = now_ts if now_ts is not None else time.time()
        return [t.to_ui_dict(now) for t in self.active_timers.values()]

    async def load_from_disk(self):
        """Load timers from disk on startup."""
        timer_data_list = await self.state_manager.load_timers()
        
        loaded_count = 0
        expired_count = 0
        
        for data in timer_data_list:
            timer = Timer.from_dict(data)
            
            if timer.is_expired():
                expired_count += 1
                await self.state_manager.delete_timer(timer.id)
                logger.info(f"Timer: Timer {timer.id} ({timer.label}) expired during downtime, skipping")
            else:
                self.active_timers[timer.id] = timer
                loaded_count += 1
                logger.info(f"Timer: Restored timer {timer.id} ({timer.label}), {timer.time_remaining():.0f}s remaining")
        
        logger.info(f"Timer: Loaded {loaded_count} active timers, {expired_count} expired skipped")
        return loaded_count, expired_count
