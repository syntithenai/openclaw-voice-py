"""
MPD (Music Player Daemon) lifecycle management for orchestrator.

Handles:
- Starting/stopping MPD process
- Configuration setup
- Graceful shutdown and resource cleanup

CONFIGURATION:
--------------
The orchestrator manages MPD lifecycle, eliminating the need for docker compose to start it.
MPD configuration is searched in standard locations:
    1. OPENCLAW_MPD_CONFIG (if set)
    2. ~/.config/mpd/mpd.conf
    3. bundled orchestrator/services/mpd.conf
    4. /etc/mpd.conf

INSTALLATION:
--------------
On Linux (Debian/Ubuntu):
  sudo apt install mpd

The MPDManager will start the local 'mpd' command; ensure it's in PATH.
"""

import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("orchestrator.services.mpd")


class MPDManager:
    """Manage MPD lifecycle within orchestrator."""

    def __init__(
        self,
        mpd_config_path: Optional[str] = None,
        mpd_port: int = 6600,
        mpd_host: str = "127.0.0.1",
        music_directory: Optional[str] = None,
        state_directory: Optional[str] = None,
        playlist_directory: Optional[str] = None,
    ):
        """
        Initialize MPD manager.

        Args:
            mpd_config_path: Path to mpd.conf file. If None, uses default locations.
            mpd_port: MPD port (default 6600)
            mpd_host: MPD bind address (default localhost)
            music_directory: Override music directory in config
            state_directory: Override state directory in config
        """
        self.mpd_config_path = mpd_config_path or self._find_mpd_config()
        self.mpd_port = mpd_port
        self.mpd_host = mpd_host
        self.music_directory = music_directory
        self.state_directory = state_directory
        self.playlist_directory = playlist_directory
        self.process: Optional[subprocess.Popen] = None
        self.pid_file: Optional[Path] = None
        self._runtime_config_path: Optional[Path] = None

    def _prepare_runtime_config(self) -> str:
        """Create an effective mpd.conf with requested directory overrides."""
        if not self.mpd_config_path:
            return ""

        config_path = Path(self.mpd_config_path)
        if not config_path.exists():
            return ""

        try:
            original = config_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Unable to read MPD config for overrides (%s): %s", config_path, exc)
            return self.mpd_config_path

        state_dir = Path(self.state_directory).expanduser() if self.state_directory else None
        playlist_dir = Path(self.playlist_directory).expanduser() if self.playlist_directory else None
        music_dir = Path(self.music_directory).expanduser() if self.music_directory else None

        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
        if playlist_dir:
            playlist_dir.mkdir(parents=True, exist_ok=True)
        if music_dir:
            music_dir.mkdir(parents=True, exist_ok=True)

        replacements: dict[str, str] = {}
        if music_dir:
            replacements["music_directory"] = str(music_dir)
        if playlist_dir:
            replacements["playlist_directory"] = str(playlist_dir)
        if state_dir:
            replacements.update(
                {
                    "db_file": str(state_dir / "database"),
                    "log_file": str(state_dir / "mpd.log"),
                    "pid_file": str(state_dir / "mpd.pid"),
                    "state_file": str(state_dir / "state"),
                    "sticker_file": str(state_dir / "sticker.sql"),
                }
            )

        if not replacements:
            return self.mpd_config_path

        if self._runtime_config_path and self._runtime_config_path.exists():
            try:
                self._runtime_config_path.unlink()
            except Exception:
                pass
            self._runtime_config_path = None

        updated = original
        for key, value in replacements.items():
            pattern = rf"(?m)^\s*{re.escape(key)}\s+\"[^\"]*\"\s*$"
            replacement = f'{key} "{value}"'
            if re.search(pattern, updated):
                updated = re.sub(pattern, replacement, updated)
            else:
                updated += f"\n{replacement}\n"

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="openclaw-mpd-",
            suffix=".conf",
            delete=False,
        )
        with tmp:
            tmp.write(updated)

        self._runtime_config_path = Path(tmp.name)
        logger.info("Using generated MPD runtime config: %s", self._runtime_config_path)
        return str(self._runtime_config_path)

    def _find_mpd_config(self) -> str:
        """Find mpd.conf in standard locations."""
        env_override = os.environ.get("OPENCLAW_MPD_CONFIG", "").strip()
        if env_override:
            candidate = Path(env_override).expanduser()
            if candidate.exists():
                logger.debug("Found mpd.conf via OPENCLAW_MPD_CONFIG: %s", candidate)
                return str(candidate)
            logger.warning("OPENCLAW_MPD_CONFIG is set but file does not exist: %s", candidate)

        candidates = [
            Path.home() / ".config/mpd/mpd.conf",
            Path(__file__).with_name("mpd.conf"),
            Path("/etc/mpd.conf"),
        ]

        for candidate in candidates:
            if candidate.exists():
                logger.debug("Found mpd.conf at: %s", candidate)
                return str(candidate)

        logger.warning(
            "mpd.conf not found in standard locations; will use default MPD config"
        )
        return ""

    def start(self) -> bool:
        """
        Start MPD process.

        Returns:
            True if MPD started successfully, False otherwise.
        """
        if self.process is not None and self.process.poll() is None:
            logger.info("MPD already running (PID %d)", self.process.pid)
            return True

        try:
            logger.info("Starting MPD (port %d)...", self.mpd_port)

            # Prepare MPD command
            cmd = ["mpd"]

            # Add config file if found
            effective_config_path = self._prepare_runtime_config()
            if effective_config_path:
                logger.debug("Using MPD config: %s", effective_config_path)
                cmd.append(effective_config_path)
            else:
                logger.debug("Using default MPD configuration (no config file specified)")

            # Optional: add command-line overrides
            # These can override config file settings if needed
            env = os.environ.copy()

            logger.debug("Executing: %s", " ".join(cmd))

            # Start MPD process
            # Use stdout/stderr for logging if available
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                # Don't kill on parent process termination
                preexec_fn=None if os.name == "nt" else lambda: None,
            )

            # Give it 1 second to start and validate PID
            time.sleep(1)
            returncode = self.process.poll()

            if returncode is not None:
                # Process exited immediately (error)
                stdout, stderr = self.process.communicate()
                stderr_text = stderr.decode("utf-8", errors="replace") if stderr else "(empty)"
                logger.error(
                    "MPD failed to start (exit code %d). stderr: %s",
                    returncode,
                    stderr_text,
                )
                # Try to give helpful guidance
                if "command not found" in stderr_text.lower():
                    logger.error(
                        "MPD not found in PATH. Install with: sudo apt install mpd"
                    )
                self.process = None
                return False

            logger.info("✓ MPD started (PID %d, listening on port %d)", self.process.pid, self.mpd_port)
            return True

        except FileNotFoundError:
            logger.error("mpd command not found. Install MPD: apt install mpd (Debian/Ubuntu)")
            return False
        except Exception as exc:
            logger.error("Failed to start MPD: %s", exc)
            return False

    def stop(self) -> bool:
        """
        Stop MPD process gracefully.

        Returns:
            True if MPD stopped successfully or was not running, False on error.
        """
        if self.process is None or self.process.poll() is not None:
            logger.debug("MPD not running")
            return True

        try:
            logger.info("Stopping MPD (PID %d)...", self.process.pid)

            # Send SIGTERM for graceful shutdown
            self.process.terminate()

            # Wait up to 5 seconds for graceful shutdown
            try:
                self.process.wait(timeout=5)
                logger.info("✓ MPD stopped gracefully")
                return True
            except subprocess.TimeoutExpired:
                logger.warning("MPD did not stop within 5s, sending SIGKILL...")
                self.process.kill()
                self.process.wait()
                logger.info("✓ MPD killed")
                return True

        except Exception as exc:
            logger.error("Error stopping MPD: %s", exc)
            return False
        finally:
            self.process = None

    def is_running(self) -> bool:
        """Check if MPD process is running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_pid(self) -> Optional[int]:
        """Get MPD process PID, or None if not running."""
        if self.is_running():
            return self.process.pid
        return None

    def restart(self) -> bool:
        """Restart MPD (stop then start)."""
        logger.info("Restarting MPD...")
        self.stop()
        time.sleep(0.5)
        return self.start()

    def wait_for_ready(self, timeout_sec: float = 10) -> bool:
        """
        Wait for MPD to be ready for connections.

        Attempts to connect via socket/TCP to validate readiness.

        Args:
            timeout_sec: Maximum time to wait in seconds

        Returns:
            True if MPD is ready, False on timeout
        """
        import socket

        start = time.monotonic()
        sock = None

        while time.monotonic() - start < timeout_sec:
            if not self.is_running():
                logger.warning("MPD process died while waiting for readiness")
                return False

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect((self.mpd_host, self.mpd_port))
                sock.close()
                logger.info("✓ MPD is ready (port %d)", self.mpd_port)
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                sock = None
                time.sleep(0.2)
            finally:
                if sock:
                    sock.close()

        logger.error("MPD did not become ready within %ds", timeout_sec)
        return False

    def cleanup(self) -> None:
        """Clean up resources (stop process, remove temp files)."""
        self.stop()
        if self._runtime_config_path and self._runtime_config_path.exists():
            try:
                self._runtime_config_path.unlink()
            except Exception:
                pass
            self._runtime_config_path = None
