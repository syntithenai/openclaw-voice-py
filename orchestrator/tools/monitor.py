"""Background monitoring for timer expiration and alarm triggering."""

import asyncio
import logging
import time
from typing import Optional, Callable
from .timer import TimerManager
from .alarm import AlarmManager

logger = logging.getLogger("orchestrator.tools.monitor")


class ToolMonitor:
    """Background task to monitor timers and alarms."""
    
    def __init__(
        self,
        timer_manager: TimerManager,
        alarm_manager: AlarmManager,
        check_interval_ms: int = 100
    ):
        """
        Initialize monitor.
        
        Args:
            timer_manager: Timer manager instance
            alarm_manager: Alarm manager instance
            check_interval_ms: How often to check for expiration/triggering (milliseconds)
        """
        self.timer_manager = timer_manager
        self.alarm_manager = alarm_manager
        self.check_interval = check_interval_ms / 1000.0
        
        self.should_stop = False
        self.monitor_task: Optional[asyncio.Task] = None
        
        # Callbacks for notifications
        self.on_timer_expired: Optional[Callable] = None
        self.on_alarm_triggered: Optional[Callable] = None
        self.on_alarm_ringing: Optional[Callable] = None
        self.defer_processing: Optional[Callable[[], bool]] = None

    def _should_defer(self) -> bool:
        if self.defer_processing is None:
            return False
        try:
            return bool(self.defer_processing())
        except Exception as exc:
            logger.debug("ToolMonitor: defer predicate failed (%s)", exc)
            return False
    
    async def start(self):
        """Start monitoring."""
        if self.monitor_task:
            logger.warning("ToolMonitor: Already running")
            return
        
        self.should_stop = False
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("ToolMonitor: Started monitoring loop")
    
    async def stop(self):
        """Stop monitoring."""
        self.should_stop = True
        if self.monitor_task:
            await self.monitor_task
            self.monitor_task = None
        logger.info("ToolMonitor: Stopped monitoring loop")
    
    async def _monitor_loop(self):
        """Main monitoring loop."""
        while not self.should_stop:
            try:
                # Check timers
                await self._check_timers()
                
                # Check alarms
                await self._check_alarms()
                
                # Sleep before next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"ToolMonitor: Error in monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    async def _invoke_monitor_callback(self, callback: Callable, obj) -> None:
        """Invoke callback with modern (id, label) signature, with legacy fallback.

        Preferred callback signature:
            callback(item_id: str, name: str)

        Legacy fallback:
            callback(item_obj)
        """
        try:
            result = callback(obj.id, getattr(obj, "label", ""))
            if asyncio.iscoroutine(result):
                await result
            return
        except TypeError:
            pass

        result = callback(obj)
        if asyncio.iscoroutine(result):
            await result
    
    async def _check_timers(self):
        """Check for expired timers."""
        active_timers = self.timer_manager.list_active_timers()
        
        for timer in active_timers:
            if timer.is_expired() and not timer.completed:
                if self._should_defer():
                    logger.debug("ToolMonitor: Deferring expired timer %s while processing is deferred", timer.id)
                    continue
                logger.info(f"ToolMonitor: Timer {timer.id} ({timer.label}) expired")
                
                # Invoke callback if set
                if self.on_timer_expired:
                    try:
                        await self._invoke_monitor_callback(self.on_timer_expired, timer)
                    except Exception as e:
                        logger.error(f"ToolMonitor: Timer callback failed: {e}")
                
                # Also invoke timer's own callback if set
                if timer.callback:
                    try:
                        if asyncio.iscoroutinefunction(timer.callback):
                            await timer.callback(timer)
                        else:
                            timer.callback(timer)
                    except Exception as e:
                        logger.error(f"ToolMonitor: Timer's callback failed: {e}")
                
                # Mark as completed and delete
                await self.timer_manager.complete_timer(timer.id)
    
    async def _check_alarms(self):
        """Check for alarms that should trigger."""
        alarms = self.alarm_manager.list_alarms()
        
        for alarm in alarms:
            # Check if alarm should trigger
            if alarm.should_trigger():
                if self._should_defer():
                    logger.debug("ToolMonitor: Deferring alarm trigger %s while processing is deferred", alarm.id)
                    continue
                logger.info(f"ToolMonitor: Alarm {alarm.id} ({alarm.label}) triggered")
                
                # Trigger alarm (starts ringing)
                await self.alarm_manager.trigger_alarm(alarm.id)
                
                # Invoke callback if set
                if self.on_alarm_triggered:
                    try:
                        await self._invoke_monitor_callback(self.on_alarm_triggered, alarm)
                    except Exception as e:
                        logger.error(f"ToolMonitor: Alarm trigger callback failed: {e}")
                
                # Also invoke alarm's own callback if set
                if alarm.callback:
                    try:
                        if asyncio.iscoroutinefunction(alarm.callback):
                            await alarm.callback(alarm)
                        else:
                            alarm.callback(alarm)
                    except Exception as e:
                        logger.error(f"ToolMonitor: Alarm's callback failed: {e}")
            
            # Check if alarm is ringing (for continuous sound loop)
            elif alarm.ringing:
                if self._should_defer():
                    logger.debug("ToolMonitor: Deferring alarm ringing callback for %s", alarm.id)
                    continue
                # Auto-stop alarm after 1 minute of ringing
                max_ring_secs = 60.0
                if alarm.triggered_at is not None and (time.time() - alarm.triggered_at) > max_ring_secs:
                    logger.info(
                        f"ToolMonitor: Alarm {alarm.id} ({alarm.label}) auto-stopped after "
                        f"{max_ring_secs:.0f}s of ringing"
                    )
                    await self.alarm_manager.stop_alarm(alarm.id)
                    continue

                if self.on_alarm_ringing:
                    try:
                        await self._invoke_monitor_callback(self.on_alarm_ringing, alarm)
                    except Exception as e:
                        logger.error(f"ToolMonitor: Alarm ringing callback failed: {e}")
    
    def set_timer_callback(self, callback: Callable):
        """Set callback for timer expiration."""
        self.on_timer_expired = callback
    
    def set_alarm_trigger_callback(self, callback: Callable):
        """Set callback for alarm triggering."""
        self.on_alarm_triggered = callback
    
    def set_alarm_ringing_callback(self, callback: Callable):
        """Set callback for alarm ringing (continuous)."""
        self.on_alarm_ringing = callback
