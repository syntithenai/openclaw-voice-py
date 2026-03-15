"""Alarm implementation with file-based persistence."""

import time
import logging
from dataclasses import dataclass
from typing import Optional, Callable, List
from datetime import datetime
from .uuid_utils import generate_uuidv7
from .state import StateManager

logger = logging.getLogger("orchestrator.tools.alarm")


@dataclass
class Alarm:
    """Alarm data structure."""
    id: str
    trigger_time: float  # Unix timestamp
    created_at: float
    label: str = ""
    enabled: bool = True
    triggered: bool = False
    ringing: bool = False
    callback: Optional[Callable] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence (excluding callback)."""
        return {
            'type': 'alarm',
            'id': self.id,
            'trigger_time': self.trigger_time,
            'created_at': self.created_at,
            'label': self.label,
            'enabled': self.enabled,
            'triggered': self.triggered,
            'ringing': self.ringing,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Alarm':
        """Create Alarm from dictionary."""
        return cls(
            id=data['id'],
            trigger_time=data['trigger_time'],
            created_at=data['created_at'],
            label=data.get('label', ''),
            enabled=data.get('enabled', True),
            triggered=data.get('triggered', False),
            ringing=data.get('ringing', False),
        )
    
    def should_trigger(self) -> bool:
        """Check if alarm should trigger now."""
        return self.enabled and not self.triggered and time.time() >= self.trigger_time
    
    def time_until(self) -> float:
        """Get time until alarm in seconds."""
        return max(0, self.trigger_time - time.time())

    def to_ui_dict(self, now_ts: float | None = None) -> dict:
        """Return lightweight dict for web UI serialization."""
        ts = now_ts if now_ts is not None else time.time()
        return {
            "id": self.id,
            "label": self.label,
            "kind": "alarm",
            "remaining_seconds": max(0.0, self.trigger_time - ts),
            "trigger_time": self.trigger_time,
            "ringing": bool(self.ringing),
            "enabled": bool(self.enabled),
            "triggered": bool(self.triggered),
        }


class AlarmManager:
    """Manages alarms."""
    
    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.alarms: dict[str, Alarm] = {}
        self.ringing_alarms: set[str] = set()
    
    async def set_alarm(
        self,
        trigger_time: float,
        label: str = "",
        callback: Optional[Callable] = None
    ) -> str:
        """
        Create a new alarm.
        
        Args:
            trigger_time: Unix timestamp when alarm should trigger
            label: Optional label/name for the alarm
            callback: Optional callback to invoke on trigger
            
        Returns:
            Alarm ID
        """
        alarm_id = generate_uuidv7()
        now = time.time()
        
        alarm = Alarm(
            id=alarm_id,
            trigger_time=trigger_time,
            created_at=now,
            label=label,
            callback=callback
        )
        
        self.alarms[alarm_id] = alarm
        
        # Persist to disk
        await self.state_manager.write_alarm(alarm_id, alarm.to_dict(), critical=True)
        
        trigger_dt = datetime.fromtimestamp(trigger_time)
        logger.info(
            f"Alarm: Created alarm {alarm_id} ({label or 'unlabeled'}) "
            f"for {trigger_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        return alarm_id
    
    async def cancel_alarm(self, alarm_id: str) -> bool:
        """
        Cancel/delete an alarm by ID.
        
        Args:
            alarm_id: Alarm identifier
            
        Returns:
            True if cancelled, False if not found
        """
        alarm = self.alarms.get(alarm_id)
        if not alarm:
            logger.warning(f"Alarm: Cannot cancel alarm {alarm_id} - not found")
            return False
        
        alarm.enabled = False
        self.alarms.pop(alarm_id, None)
        self.ringing_alarms.discard(alarm_id)
        
        # Delete from disk
        await self.state_manager.delete_alarm(alarm_id)
        await self.state_manager.log_event('alarm_cancelled', {'alarm_id': alarm_id, 'label': alarm.label})
        
        logger.info(f"Alarm: Cancelled alarm {alarm_id} ({alarm.label})")
        return True

    async def cancel_alarm_by_label(self, label: str) -> int:
        """Cancel future alarms by label.

        Returns the number of alarms cancelled.
        """
        target = (label or "").strip().lower()
        if not target:
            return 0

        alarm_ids = [
            alarm_id
            for alarm_id, alarm in self.alarms.items()
            if alarm.enabled and not alarm.triggered and (alarm.label or "").strip().lower() == target
        ]

        cancelled = 0
        for alarm_id in alarm_ids:
            if await self.cancel_alarm(alarm_id):
                cancelled += 1
        return cancelled
    
    async def stop_alarm(self, alarm_id: Optional[str] = None) -> int:
        """
        Stop actively ringing alarm(s).
        
        Args:
            alarm_id: Specific alarm ID to stop, or None to stop all ringing alarms
            
        Returns:
            Number of alarms stopped
        """
        if alarm_id:
            # Stop specific alarm
            alarm = self.alarms.get(alarm_id)
            if alarm and alarm.ringing:
                alarm.ringing = False
                self.ringing_alarms.discard(alarm_id)
                await self.state_manager.write_alarm(alarm_id, alarm.to_dict(), critical=True)
                await self.state_manager.log_event('alarm_stopped', {'alarm_id': alarm_id, 'label': alarm.label})
                logger.info(f"Alarm: Stopped alarm {alarm_id} ({alarm.label})")
                return 1
            return 0
        else:
            # Stop all ringing alarms
            stopped_count = 0
            alarm_ids = list(self.ringing_alarms)
            
            for aid in alarm_ids:
                alarm = self.alarms.get(aid)
                if alarm:
                    alarm.ringing = False
                    await self.state_manager.write_alarm(aid, alarm.to_dict(), critical=True)
                    await self.state_manager.log_event('alarm_stopped', {'alarm_id': aid, 'label': alarm.label})
                    logger.info(f"Alarm: Stopped alarm {aid} ({alarm.label})")
                    stopped_count += 1
            
            self.ringing_alarms.clear()
            return stopped_count
    
    async def stop_alarm_by_label(self, label: str) -> int:
        """
        Stop ringing alarms by label.
        
        Args:
            label: Alarm label
            
        Returns:
            Number of alarms stopped
        """
        stopped_count = 0
        alarms_to_stop = [
            alarm_id for alarm_id in self.ringing_alarms
            if self.alarms[alarm_id].label.lower() == label.lower()
        ]
        
        for alarm_id in alarms_to_stop:
            await self.stop_alarm(alarm_id)
            stopped_count += 1
        
        return stopped_count
    
    async def trigger_alarm(self, alarm_id: str):
        """
        Trigger an alarm (start ringing).
        
        Args:
            alarm_id: Alarm identifier
        """
        alarm = self.alarms.get(alarm_id)
        if not alarm:
            return
        
        alarm.triggered = True
        alarm.ringing = True
        self.ringing_alarms.add(alarm_id)
        
        # Write triggered immediately
        await self.state_manager.write_alarm(alarm_id, alarm.to_dict(), critical=True)
        await self.state_manager.log_event('alarm_triggered', {'alarm_id': alarm_id, 'label': alarm.label})
        
        logger.info(f"Alarm: Triggered alarm {alarm_id} ({alarm.label})")
    
    async def update_ringing_state(self, alarm_id: str, ringing: bool):
        """
        Update alarm ringing state (debounced write).
        
        Args:
            alarm_id: Alarm identifier
            ringing: New ringing state
        """
        alarm = self.alarms.get(alarm_id)
        if not alarm:
            return
        
        alarm.ringing = ringing
        if ringing:
            self.ringing_alarms.add(alarm_id)
        else:
            self.ringing_alarms.discard(alarm_id)
        
        # Debounced write for ringing state
        await self.state_manager.write_alarm(alarm_id, alarm.to_dict(), critical=False)
    
    def get_alarm(self, alarm_id: str) -> Optional[Alarm]:
        """Get alarm by ID."""
        return self.alarms.get(alarm_id)
    
    def list_alarms(self) -> List[Alarm]:
        """Get list of all alarms."""
        return list(self.alarms.values())
    
    def list_ringing_alarms(self) -> List[Alarm]:
        """Get list of currently ringing alarms."""
        return [self.alarms[aid] for aid in self.ringing_alarms if aid in self.alarms]

    def list_ui_alarms(self, now_ts: float | None = None) -> list:
        """Return alarms as UI-ready dicts (including ringing alarms)."""
        now = now_ts if now_ts is not None else time.time()
        return [
            alarm.to_ui_dict(now)
            for alarm in self.alarms.values()
            if alarm.enabled or alarm.ringing
        ]
    
    async def load_from_disk(self):
        """Load alarms from disk on startup."""
        alarm_data_list = await self.state_manager.load_alarms()
        
        now = time.time()
        loaded_count = 0
        missed_count = 0
        MISSED_WINDOW = 3600  # 1 hour
        
        for data in alarm_data_list:
            alarm = Alarm.from_dict(data)
            
            # Check if alarm was missed during downtime
            if alarm.enabled and not alarm.triggered and alarm.trigger_time < now:
                missed_delta = now - alarm.trigger_time
                if missed_delta < MISSED_WINDOW:
                    # Trigger now if within window
                    alarm.triggered = True
                    alarm.ringing = True
                    self.ringing_alarms.add(alarm.id)
                    missed_count += 1
                    logger.info(f"Alarm: Alarm {alarm.id} ({alarm.label}) missed during downtime, triggering now")
                else:
                    # Too old, skip
                    await self.state_manager.delete_alarm(alarm.id)
                    logger.info(f"Alarm: Alarm {alarm.id} ({alarm.label}) missed during downtime (too old), skipping")
                    continue
            
            self.alarms[alarm.id] = alarm
            
            # Resume ringing state if was ringing
            if alarm.ringing:
                self.ringing_alarms.add(alarm.id)
            
            loaded_count += 1
            if alarm.enabled and not alarm.triggered:
                logger.info(f"Alarm: Restored alarm {alarm.id} ({alarm.label}), triggers in {alarm.time_until():.0f}s")
            elif alarm.ringing:
                logger.info(f"Alarm: Restored ringing alarm {alarm.id} ({alarm.label})")
        
        logger.info(f"Alarm: Loaded {loaded_count} alarms, {missed_count} fired due to missed trigger")
        return loaded_count, missed_count
