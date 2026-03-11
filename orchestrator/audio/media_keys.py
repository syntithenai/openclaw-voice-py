"""
Media Key Detection - Capture hardware button presses from USB/Bluetooth devices.

Monitors input devices for media control keys (play/pause, volume, mute, phone).
These typically come from conference speakers, headsets, and keyboards.
Supports long-press detection for gesture recognition (e.g., hold play for 0.5s = wake).
"""

import asyncio
import errno
import logging
import time
import os
import grp
from typing import Any, Callable, Optional, List, Dict, Set
from dataclasses import dataclass
import threading

try:
    import evdev
    from evdev import InputDevice, categorize, ecodes
    EVDEV_AVAILABLE = True
except ImportError:
    evdev = None
    categorize = None
    ecodes = None
    EVDEV_AVAILABLE = False

logger = logging.getLogger(__name__)


def _active_group_names() -> List[str]:
    names: List[str] = []
    try:
        for gid in os.getgroups():
            try:
                names.append(grp.getgrgid(gid).gr_name)
            except KeyError:
                continue
    except Exception:
        pass
    return names


@dataclass
class MediaKeyEvent:
    """Represents a media key button press or gesture."""
    key: str  # "play_pause", "volume_up", "volume_down", "mute", "phone", "play_pause_long" etc
    device_name: str
    timestamp: float
    event_type: str = "press"  # "press", "release", "long_press"


class MediaKeyDetector:
    """
    Detects media key presses from USB/Bluetooth audio devices.
    
    Usage:
        detector = MediaKeyDetector()
        detector.set_callback(lambda event: print(f"Button: {event.key}"))
        await detector.start()
    """
    
    # Map evdev key codes to friendly names
    KEY_MAP = (
        {
            ecodes.KEY_PLAYPAUSE: "play_pause",
            ecodes.KEY_PLAY: "play",
            ecodes.KEY_PAUSE: "pause",
            ecodes.KEY_STOP: "stop",
            # Many conference speakerphone devices expose STOPCD for call/phone button
            # (e.g., Actions Anker PowerConf on Linux input stack)
            ecodes.KEY_STOPCD: "phone",
            ecodes.KEY_NEXTSONG: "next",
            ecodes.KEY_PREVIOUSSONG: "previous",
            ecodes.KEY_VOLUMEUP: "volume_up",
            ecodes.KEY_VOLUMEDOWN: "volume_down",
            ecodes.KEY_MUTE: "mute",
            ecodes.KEY_PHONE: "phone",  # Conference speaker phone button
            # Some devices map call controls to these keycodes instead of KEY_PHONE
            ecodes.KEY_FASTFORWARD: "phone",
        }
        if EVDEV_AVAILABLE
        else {}
    )

    # Add optional keys only when present in kernel/input headers for current system
    _OPTIONAL_PHONE_KEYS = (
        "KEY_PICKUP_PHONE",
        "KEY_HANGUP_PHONE",
        "KEY_VOICECOMMAND",
    )
    for _key_name in _OPTIONAL_PHONE_KEYS:
        _code = getattr(ecodes, _key_name, None)
        if _code is not None:
            KEY_MAP[_code] = "phone"

    # Hard safety gate: never treat full keyboard-like HID devices as media-key
    # capture targets. Even if MEDIA_KEYS_DEVICE_FILTER is blank/misconfigured,
    # this prevents accidental exclusive grabs of keyboard/mouse combo devices.
    KEYBOARD_INDICATOR_KEYS = (
        {
            ecodes.KEY_A,
            ecodes.KEY_B,
            ecodes.KEY_C,
            ecodes.KEY_D,
            ecodes.KEY_E,
            ecodes.KEY_F,
            ecodes.KEY_G,
            ecodes.KEY_H,
            ecodes.KEY_I,
            ecodes.KEY_J,
            ecodes.KEY_K,
            ecodes.KEY_L,
            ecodes.KEY_M,
            ecodes.KEY_N,
            ecodes.KEY_O,
            ecodes.KEY_P,
            ecodes.KEY_Q,
            ecodes.KEY_R,
            ecodes.KEY_S,
            ecodes.KEY_T,
            ecodes.KEY_U,
            ecodes.KEY_V,
            ecodes.KEY_W,
            ecodes.KEY_X,
            ecodes.KEY_Y,
            ecodes.KEY_Z,
            ecodes.KEY_SPACE,
            ecodes.KEY_ENTER,
            ecodes.KEY_LEFTSHIFT,
            ecodes.KEY_RIGHTSHIFT,
            ecodes.KEY_LEFTCTRL,
            ecodes.KEY_RIGHTCTRL,
            ecodes.KEY_LEFTALT,
            ecodes.KEY_RIGHTALT,
        }
        if EVDEV_AVAILABLE
        else set()
    )

    BLOCKED_DEVICE_NAME_TOKENS = {
        "keyboard",
        "mouse",
        "touchpad",
        "trackpad",
        "pointer",
        "gpio-keys",
    }

    ALLOWED_SPEAKER_HINT_TOKENS = {
        "speaker",
        "conference",
        "anker",
        "powerconf",
        "headset",
        "burr-brown",
        "usb audio",
    }

    def _is_blocked_device_name(self, device_name: str) -> bool:
        """Explicit name blocklist for keyboard/mouse-like devices."""
        normalized = (device_name or "").strip().lower()
        if not normalized:
            return False
        if any(token in normalized for token in self.ALLOWED_SPEAKER_HINT_TOKENS):
            return False
        return any(token in normalized for token in self.BLOCKED_DEVICE_NAME_TOKENS)

    def _looks_like_keyboard_device(self, key_codes: List[int]) -> bool:
        """Heuristic guardrail to reject full keyboard-like input devices."""
        if not key_codes:
            return False

        if len(key_codes) >= 30:
            return True

        indicator_count = sum(1 for code in self.KEYBOARD_INDICATOR_KEYS if code in key_codes)
        return indicator_count >= 6
    
    @staticmethod
    def parse_scan_code_list(scan_codes: Optional[str]) -> Set[int]:
        """Parse comma-separated scan codes (supports decimal or hex like 0xc00b6)."""
        if not scan_codes:
            return set()

        parsed: Set[int] = set()
        for raw_part in scan_codes.split(","):
            part = raw_part.strip().lower()
            if not part:
                continue
            try:
                parsed.add(int(part, 0))
            except ValueError:
                logger.warning("Ignoring invalid media scan code value: %s", raw_part)
        return parsed

    def __init__(
        self,
        device_filter: Optional[str] = None,
        long_press_threshold: float = 0.5,
        play_scan_codes: Optional[str] = "0xc00b6,0xc00cd",
        command_debounce_ms: int = 400,
        exclusive_grab: bool = False,
    ):
        """
        Args:
            device_filter: Optional substring to filter device names (e.g., "Anker")
            long_press_threshold: Time in seconds to detect as long press (default 0.5s)
        """
        if not EVDEV_AVAILABLE:
            raise ImportError("evdev library not installed. Run: pip install evdev")
        
        self.device_filter = device_filter
        self.long_press_threshold = long_press_threshold
        self.play_scan_codes = self.parse_scan_code_list(play_scan_codes)
        self.command_debounce_s = max(0.0, command_debounce_ms / 1000.0)
        self.exclusive_grab = bool(exclusive_grab)
        self.callback: Optional[Callable[[MediaKeyEvent], None]] = None
        self.devices: List[Any] = []
        self.running = False
        self._tasks = []
        self._device_paths = set()
        self._rescan_task: Optional[asyncio.Task] = None
        
        # Track pressed keys for long-press detection: {(device_name, key_code): press_time}
        self._key_press_times: Dict[tuple, float] = {}
        # Debounce mapped MSC_SCAN events: {(device_name, scan_value): last_timestamp}
        self._scan_last_seen_ts: Dict[tuple, float] = {}
        self._scan_debounce_s: float = 0.1  # 100ms - filters duplicate events from single press, allows rapid taps
        # Debounce emitted commands: {(device_name, logical_key): last_timestamp}
        self._command_last_seen_ts: Dict[tuple, float] = {}

    async def _dispatch_media_event(self, media_event: MediaKeyEvent) -> None:
        """Dispatch a logical media event with per-command debounce."""
        if not self.callback:
            return

        debounce_key = (media_event.device_name, media_event.key)
        last_seen = self._command_last_seen_ts.get(debounce_key, 0.0)
        if self.command_debounce_s > 0 and (media_event.timestamp - last_seen) < self.command_debounce_s:
            logger.info(
                "Debounced duplicate media command from %s: %s (%.0fms window)",
                media_event.device_name,
                media_event.key,
                self.command_debounce_s * 1000.0,
            )
            return

        self._command_last_seen_ts[debounce_key] = media_event.timestamp

        try:
            result = self.callback(media_event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Error in media key callback: {e}", exc_info=True)
    
    def set_callback(self, callback: Callable[[MediaKeyEvent], None]):
        """Set callback function to receive media key events."""
        self.callback = callback
    
    def find_media_devices(self) -> List[Any]:
        """
        Find all input devices that have media key capabilities.
        
        Returns:
            List of InputDevice objects that support media keys
        """
        devices = []
        for path in evdev.list_devices():
            try:
                device = InputDevice(path)
                
                # Check if device has any media key capabilities
                caps = device.capabilities(verbose=False)
                if ecodes.EV_KEY not in caps:
                    continue
                
                key_codes = caps[ecodes.EV_KEY]
                has_media_keys = any(code in self.KEY_MAP for code in key_codes)
                has_misc_scan = ecodes.EV_MSC in caps
                filter_matches = (
                    self.device_filter is not None
                    and self.device_filter.lower() in device.name.lower()
                )

                if self._is_blocked_device_name(device.name):
                    logger.warning(
                        "Skipping blocked input device name for media capture safety: %s (%s)",
                        device.name,
                        path,
                    )
                    device.close()
                    continue

                if self._looks_like_keyboard_device(key_codes):
                    logger.warning(
                        "Skipping keyboard-like input device for media capture safety: %s (%s)",
                        device.name,
                        path,
                    )
                    device.close()
                    continue
                
                if has_media_keys or (filter_matches and (key_codes or has_misc_scan)):
                    # Apply optional filter
                    if self.device_filter and not filter_matches:
                        logger.debug(f"Skipping device (filter mismatch): {device.name}")
                        continue

                    if has_media_keys:
                        logger.info(f"Found media device: {device.name} ({path})")
                    else:
                        logger.info(
                            "Found filtered input device with non-standard media mapping: %s (%s)",
                            device.name,
                            path,
                        )
                    devices.append(device)
                else:
                    device.close()
            except (OSError, PermissionError) as e:
                logger.debug(f"Cannot access {path}: {e}")
        
        if not devices:
            active_groups = _active_group_names()
            if os.geteuid() != 0 and "input" not in active_groups:
                logger.warning(
                    "No media key devices found. Current process is not in active 'input' group (active groups: %s). "
                    "Open a new login shell or run 'newgrp input' before starting orchestrator.",
                    ",".join(active_groups) if active_groups else "unknown",
                )
            else:
                logger.warning("No media key devices found. Check permissions or device connection.")
        
        return devices
    
    async def start(self):
        """Start monitoring for media key events."""
        added = self._add_new_devices(self.find_media_devices())

        if not self.devices:
            logger.warning("No media devices found to monitor")
            return

        self.running = True

        if added:
            logger.info(f"Media key detector started with {len(self.devices)} device(s)")
    
    async def stop(self):
        """Stop monitoring and release devices."""
        self.running = False
        
        # Cancel all monitoring tasks
        for task in self._tasks:
            task.cancel()
        
        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._rescan_task:
            self._rescan_task.cancel()
            await asyncio.gather(self._rescan_task, return_exceptions=True)
            self._rescan_task = None
        
        # Close all devices
        for device in self.devices:
            device.close()
        
        self.devices.clear()
        self._tasks.clear()
        self._device_paths.clear()
        logger.info("Media key detector stopped")

    def _add_new_devices(self, discovered_devices: List[Any]) -> int:
        """Register newly discovered devices and start monitoring tasks for them."""
        added = 0
        for device in discovered_devices:
            if device.path in self._device_paths:
                try:
                    device.close()
                except Exception:
                    pass
                continue

            self._device_paths.add(device.path)

            if self.exclusive_grab:
                try:
                    device.grab()
                    logger.debug(f"Grabbed exclusive access to {device.name}")
                except Exception as e:
                    logger.error(
                        "Could not grab exclusive access to %s (%s): %s. "
                        "Skipping device so OS media handling remains explicit.",
                        device.name,
                        device.path,
                        e,
                    )
                    try:
                        device.close()
                    except Exception:
                        pass
                    self._device_paths.discard(device.path)
                    continue

            self.devices.append(device)
            added += 1

            task = asyncio.create_task(self._monitor_device(device))
            self._tasks.append(task)

        return added

    def _remove_device(self, device: Any):
        """Remove a device from internal tracking and close it."""
        self._device_paths.discard(getattr(device, "path", None))
        self.devices = [d for d in self.devices if getattr(d, "path", None) != getattr(device, "path", None)]
        try:
            device.close()
        except Exception:
            pass

    def _schedule_rescan(self):
        """Schedule a background rescan if one is not already running."""
        if not self.running:
            return
        if self._rescan_task and not self._rescan_task.done():
            return
        self._rescan_task = asyncio.create_task(self._rescan_devices())

    async def _rescan_devices(self):
        """Periodically rescan for media devices after disconnect/reset."""
        await asyncio.sleep(1.0)
        while self.running:
            added = self._add_new_devices(self.find_media_devices())
            if added:
                logger.info("Media key detector reconnected %d device(s)", added)
                return
            await asyncio.sleep(1.0)
    
    async def _monitor_device(self, device: Any):
        """Monitor a single device for key events, including long-press detection."""
        logger.info(f"Monitoring device: {device.name}")
        
        try:
            # Run device reading in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            
            while self.running:
                try:
                    # Read events with timeout
                    event = await loop.run_in_executor(None, self._read_event, device)
                    
                    if event is None:
                        continue
                    
                    # Handle key press (value=1) and key release (value=0)
                    if event.type == ecodes.EV_KEY:
                        key_name = self.KEY_MAP.get(event.code)

                        if not key_name:
                            logger.info(
                                "Unknown media key event from %s: code=%s value=%s",
                                device.name,
                                event.code,
                                event.value,
                            )
                            continue
                        
                        event_time = time.time()
                        key_state_key = (device.name, event.code)
                        
                        if event.value == 1:
                            # KEY PRESS: Record the press time (don't fire callback yet)
                            self._key_press_times[key_state_key] = event_time
                            logger.debug(f"Key pressed: {key_name} on {device.name}")
                        
                        elif event.value == 0:
                            # KEY RELEASE: Determine if short or long press, then fire callback
                            if key_state_key in self._key_press_times:
                                press_time = self._key_press_times[key_state_key]
                                hold_duration = event_time - press_time
                                del self._key_press_times[key_state_key]
                                
                                # Determine event type based on hold duration
                                if hold_duration >= self.long_press_threshold:
                                    actual_event_type = "long_press"
                                    actual_key_name = f"{key_name}_long"
                                    logger.info(
                                        f"Long press detected: {key_name} ({hold_duration:.2f}s) on {device.name}"
                                    )
                                else:
                                    actual_event_type = "press"
                                    actual_key_name = key_name
                                    logger.info(f"Key pressed: {key_name} ({hold_duration:.3f}s) on {device.name}")
                                
                                # Fire callback only on release (after we know if it was long or short)
                                media_event = MediaKeyEvent(
                                    key=actual_key_name,
                                    device_name=device.name,
                                    timestamp=event_time,
                                    event_type=actual_event_type
                                )
                                
                                await self._dispatch_media_event(media_event)
                            else:
                                # Release without a recorded press (shouldn't happen normally)
                                logger.debug(f"Key released without press record: {key_name}")

                    elif event.type == ecodes.EV_MSC and event.code == ecodes.MSC_SCAN:
                        logger.info(
                            "MSC_SCAN event from %s: value=0x%x (%d)",
                            device.name,
                            event.value,
                            event.value,
                        )
                        if event.value in self.play_scan_codes:
                            now = time.time()
                            key = (device.name, event.value)
                            last_seen = self._scan_last_seen_ts.get(key, 0.0)
                            if now - last_seen < self._scan_debounce_s:
                                continue
                            self._scan_last_seen_ts[key] = now

                            logger.info(
                                "Mapped MSC_SCAN 0x%x from %s to play_pause",
                                event.value,
                                device.name,
                            )
                            media_event = MediaKeyEvent(
                                key="play_pause",
                                device_name=device.name,
                                timestamp=now,
                                event_type="press",
                            )
                            await self._dispatch_media_event(media_event)
                
                except asyncio.CancelledError:
                    break
                except OSError as e:
                    if e.errno == errno.ENODEV:
                        logger.warning(
                            "Media key device disappeared/reset: %s (%s)",
                            device.name,
                            device.path,
                        )
                    else:
                        logger.error(f"Device read error for {device.name}: {e}")
                    break

        except Exception as e:
            logger.error(f"Error monitoring {device.name}: {e}", exc_info=True)
        finally:
            self._remove_device(device)
            self._schedule_rescan()
    
    def _read_event(self, device: Any):
        """Read a single event from device (blocking, runs in thread)."""
        for event in device.read_loop():
            return event
        return None
    
    def list_all_devices(self):
        """List all input devices for debugging."""
        print("\n=== Available Input Devices ===")
        for path in evdev.list_devices():
            try:
                device = InputDevice(path)
                caps = device.capabilities(verbose=False)
                has_keys = ecodes.EV_KEY in caps
                
                print(f"\nDevice: {device.name}")
                print(f"  Path: {path}")
                print(f"  Has keys: {has_keys}")
                
                if has_keys:
                    key_codes = caps[ecodes.EV_KEY]
                    media_keys = [self.KEY_MAP[code] for code in key_codes if code in self.KEY_MAP]
                    if media_keys:
                        print(f"  Media keys: {', '.join(media_keys)}")
                
                device.close()
            except Exception as e:
                print(f"  Error: {e}")


async def test_media_keys():
    """Test function to detect and print media key presses."""
    detector = MediaKeyDetector()
    
    # List all devices
    detector.list_all_devices()
    
    # Set up callback
    def on_key_press(event: MediaKeyEvent):
        print(f"\n🎵 {event.key.upper()} pressed on {event.device_name}")
    
    detector.set_callback(on_key_press)
    
    # Start monitoring
    await detector.start()
    
    print("\n👂 Listening for media key presses... (Press Ctrl+C to stop)")
    
    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await detector.stop()


if __name__ == "__main__":
    asyncio.run(test_media_keys())
