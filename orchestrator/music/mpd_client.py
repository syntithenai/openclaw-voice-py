"""
MPD (Music Player Daemon) TCP client with connection pooling.

Provides low-level MPD protocol communication with:
- Automatic reconnection on connection loss
- Connection pooling for instant command execution
- Proper command parsing and response handling
"""

import asyncio
import itertools
import logging
import time
from typing import Optional, Dict, List
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


_TRACE_COMMAND_PREFIXES = ("status", "playlistinfo", "play", "random")


def _command_preview(command: str, limit: int = 120) -> str:
    text = str(command or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit-3]}..."


def _should_trace_command(command: str) -> bool:
    stripped = str(command or "").strip().lower()
    return any(stripped == prefix or stripped.startswith(f"{prefix} ") for prefix in _TRACE_COMMAND_PREFIXES)


def _log_command_timing(kind: str, command: str, conn_label: str, elapsed_ms: float, extra: str = "") -> None:
    preview = _command_preview(command)
    suffix = f" {extra}" if extra else ""
    if elapsed_ms >= 1000:
        logger.warning("⏱️ MPD %s %s on %s took %.1fms%s", kind, preview, conn_label, elapsed_ms, suffix)
    else:
        logger.info("⏱️ MPD %s %s on %s took %.1fms%s", kind, preview, conn_label, elapsed_ms, suffix)


class MPDConnection:
    """Single TCP connection to MPD server."""
    _id_counter = itertools.count(1)
    
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._last_activity_ts = 0.0
        self._conn_id = next(self._id_counter)

    @property
    def label(self) -> str:
        return f"mpd#{self._conn_id}"
    
    async def connect(self) -> bool:
        """Establish connection to MPD server."""
        started = time.monotonic()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout
            )
            
            # Read MPD welcome message
            welcome = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self.timeout
            )
            
            if not welcome.startswith(b"OK MPD"):
                logger.error(f"Invalid MPD welcome: {welcome}")
                await self.close()
                return False
            
            self._connected = True
            self._last_activity_ts = time.monotonic()
            logger.info(
                "Connected %s to MPD at %s:%s in %.1fms",
                self.label,
                self.host,
                self.port,
                (time.monotonic() - started) * 1000,
            )
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to MPD at {self.host}:{self.port}")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to MPD: {e}")
            return False
    
    async def close(self):
        """Close the connection."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")
        
        self._reader = None
        self._writer = None
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        """Check if connection is active."""
        return self._connected and self._writer is not None and not self._writer.is_closing()
    
    async def send_command(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        """
        Send a command to MPD and parse the response.
        
        Args:
            command: MPD protocol command (e.g., "status", "play", "search artist Beatles")
        
        Returns:
            Dictionary of key-value pairs from response
        
        Raises:
            ConnectionError: If not connected or connection lost
            ValueError: If MPD returns an error response
        """
        async with self._lock:
            op_timeout = float(timeout) if timeout is not None else self.timeout
            trace_command = _should_trace_command(command)
            started = time.monotonic()
            if not self.is_connected or self._reader is None or self._writer is None:
                raise ConnectionError("Not connected to MPD")
            
            try:
                if trace_command:
                    logger.info(
                        "→ MPD command %s on %s (timeout=%.1fs)",
                        _command_preview(command),
                        self.label,
                        op_timeout,
                    )
                # Send command
                self._writer.write(f"{command}\n".encode('utf-8'))
                await asyncio.wait_for(
                    self._writer.drain(),
                    timeout=op_timeout
                )
                
                # Read response
                response = {}
                while True:
                    # Re-check connection state in case close() was called while reading
                    if self._reader is None:
                        raise ConnectionError("Connection lost to MPD during command")
                    
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=op_timeout
                    )
                    
                    if not line:
                        raise ConnectionError("Connection closed by MPD")
                    
                    line = line.decode('utf-8').strip()
                    
                    # Check for completion
                    if line == "OK":
                        break
                    
                    # Check for errors
                    if line.startswith("ACK"):
                        error_msg = line[4:].strip() if len(line) > 4 else "Unknown error"
                        raise ValueError(f"MPD error: {error_msg}")
                    
                    # Parse key-value pair
                    if ": " in line:
                        key, value = line.split(": ", 1)
                        # Handle multiple values for same key (e.g., multiple files)
                        if key in response:
                            # Convert to list if needed
                            if not isinstance(response[key], list):
                                response[key] = [response[key]]
                            response[key].append(value)
                        else:
                            response[key] = value

                if trace_command:
                    extra = f"keys={len(response)}"
                    _log_command_timing("command", command, self.label, (time.monotonic() - started) * 1000, extra)
                
                return response
                
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for MPD response on %s for %s", self.label, _command_preview(command))
                self._connected = False
                raise ConnectionError("MPD command timeout")
            except Exception as e:
                logger.error("Error sending MPD command on %s for %s: %s", self.label, _command_preview(command), e)
                self._connected = False
                raise
            finally:
                if self._connected:
                    self._last_activity_ts = time.monotonic()
    
    async def send_command_list(self, send_cmd: str = "", timeout: float | None = None) -> List[Dict[str, str]]:
        """
        Send command and read response for list commands that return multiple items.
        
        Args:
            send_cmd: MPD protocol command to send (if empty, just reads response)
        
        Returns:
            List of dictionaries, one per item
        """
        async with self._lock:
            op_timeout = float(timeout) if timeout is not None else self.timeout
            trace_command = _should_trace_command(send_cmd)
            started = time.monotonic()
            if not self.is_connected or self._reader is None or self._writer is None:
                raise ConnectionError("Not connected to MPD")
            
            try:
                # Send command if provided
                if send_cmd:
                    if trace_command:
                        logger.info(
                            "→ MPD list %s on %s (timeout=%.1fs)",
                            _command_preview(send_cmd),
                            self.label,
                            op_timeout,
                        )
                    self._writer.write(f"{send_cmd}\n".encode('utf-8'))
                    await asyncio.wait_for(
                        self._writer.drain(),
                        timeout=op_timeout
                    )
                
                items = []
                current_item = {}
                
                while True:
                    # Re-check connection state in case close() was called while reading
                    if self._reader is None:
                        raise ConnectionError("Connection lost to MPD during list read")
                    
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=op_timeout
                    )
                    
                    if not line:
                        self._connected = False
                        raise ConnectionError("Connection closed by MPD during list read")
                    
                    line = line.decode('utf-8').strip()
                    
                    if line == "OK":
                        if current_item:
                            items.append(current_item)
                        break
                    
                    if line.startswith("ACK"):
                        error_msg = line[4:].strip() if len(line) > 4 else "Unknown error"
                        raise ValueError(f"MPD error: {error_msg}")
                    
                    if ": " in line:
                        key, value = line.split(": ", 1)
                        
                        # New item starts when MPD emits another primary key for list-like commands.
                        # Common boundaries: file (playlist/search), outputid (outputs),
                        # playlist (listplaylists).
                        if key in {"file", "outputid", "playlist"} and current_item:
                            items.append(current_item)
                            current_item = {}
                        
                        current_item[key] = value

                if trace_command:
                    extra = f"items={len(items)}"
                    _log_command_timing("list", send_cmd, self.label, (time.monotonic() - started) * 1000, extra)
                
                return items
                
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for MPD list response on %s for %s", self.label, _command_preview(send_cmd))
                self._connected = False
                raise ConnectionError("MPD command timeout")
            except ConnectionError:
                # Connection errors are already handled
                raise
            except Exception as e:
                logger.error("Error in MPD list response on %s for %s: %s", self.label, _command_preview(send_cmd), e)
                self._connected = False
                raise ConnectionError(f"MPD list response error: {e}")
            finally:
                if self._connected:
                    self._last_activity_ts = time.monotonic()

    async def send_command_batch(self, commands: List[str], timeout: float | None = None) -> None:
        """Send multiple non-query commands via MPD command list mode."""
        async with self._lock:
            op_timeout = float(timeout) if timeout is not None else self.timeout
            if not self.is_connected or self._reader is None or self._writer is None:
                raise ConnectionError("Not connected to MPD")
            if not commands:
                return

            try:
                payload_lines = ["command_list_ok_begin", *commands, "command_list_end", ""]
                self._writer.write("\n".join(payload_lines).encode("utf-8"))
                await asyncio.wait_for(self._writer.drain(), timeout=op_timeout)

                while True:
                    if self._reader is None:
                        raise ConnectionError("Connection lost to MPD during batch command")

                    line = await asyncio.wait_for(self._reader.readline(), timeout=op_timeout)
                    if not line:
                        self._connected = False
                        raise ConnectionError("Connection closed by MPD during batch command")

                    line = line.decode("utf-8").strip()
                    if line == "OK":
                        break
                    if line == "list_OK":
                        continue
                    if line.startswith("ACK"):
                        error_msg = line[4:].strip() if len(line) > 4 else "Unknown error"
                        raise ValueError(f"MPD error: {error_msg}")

            except asyncio.TimeoutError:
                logger.error("Timeout waiting for MPD batch response")
                self._connected = False
                raise ConnectionError("MPD batch command timeout")
            except Exception as e:
                logger.error(f"Error sending MPD batch command: {e}")
                self._connected = False
                raise
            finally:
                if self._connected:
                    self._last_activity_ts = time.monotonic()

    async def ensure_alive(self, idle_probe_after_s: float = 4.0) -> bool:
        """Best-effort liveness probe for potentially stale idle sockets."""
        if not self.is_connected:
            return False
        if (time.monotonic() - self._last_activity_ts) < idle_probe_after_s:
            return True
        try:
            logger.info("↻ MPD liveness probe on %s after %.1fs idle", self.label, time.monotonic() - self._last_activity_ts)
            await self.send_command("ping", timeout=min(1.5, max(0.5, self.timeout)))
            return True
        except Exception as exc:
            logger.warning("↻ MPD liveness probe failed on %s: %s", self.label, exc)
            self._connected = False
            return False


class MPDClientPool:
    """
    Connection pool for MPD clients.
    
    Maintains a pool of connections to MPD for instant command execution.
    Handles automatic reconnection and connection health monitoring.
    """
    
    def __init__(self, host: str, port: int, pool_size: int = 3, timeout: float = 8.0):
        self.host = host
        self.port = port
        self.pool_size = pool_size
        self.timeout = timeout
        self._pool: List[MPDConnection] = []
        self._available: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def initialize(self):
        """Initialize the connection pool."""
        async with self._lock:
            if self._initialized:
                return
            
            logger.info(f"Initializing MPD connection pool (size={self.pool_size})")
            
            for i in range(self.pool_size):
                conn = MPDConnection(self.host, self.port, self.timeout)
                if await conn.connect():
                    self._pool.append(conn)
                    await self._available.put(conn)
                else:
                    logger.warning(f"Failed to initialize connection {i+1}/{self.pool_size}")
            
            if not self._pool:
                raise ConnectionError(f"Failed to establish any connections to MPD at {self.host}:{self.port}")
            
            self._initialized = True
            logger.info(f"MPD connection pool initialized with {len(self._pool)} connections")
    
    async def close(self):
        """Close all connections in the pool."""
        async with self._lock:
            # Cancel any pending FTS rebuild tasks before closing connections
            # This prevents errors when FTS rebuild tries to use closed connections
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
            
            # Clear the queue
            while not self._available.empty():
                try:
                    self._available.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            self._initialized = False
            logger.info("MPD connection pool closed")
    
    @asynccontextmanager
    async def get_connection(self):
        """
        Get a connection from the pool (context manager).
        
        Usage:
            async with pool.get_connection() as conn:
                result = await conn.send_command("status")
        """
        if not self._initialized:
            await self.initialize()
        
        # Get connection from pool
        conn: MPDConnection = await self._available.get()

        try:
            # Check if connection is still valid, reconnect if needed
            if not conn.is_connected:
                logger.info("↻ Reconnecting stale %s", conn.label)
                if not await conn.connect():
                    logger.warning("Failed to reconnect to MPD; keeping connection in pool for future retry")
                    raise ConnectionError("Failed to reconnect to MPD")
            elif not await conn.ensure_alive(idle_probe_after_s=4.0):
                logger.warning("↻ %s failed liveness probe; reconnecting", conn.label)
                await conn.close()
                if not await conn.connect():
                    raise ConnectionError("Failed to reconnect to MPD")

            yield conn
        finally:
            # Always return connection to pool to avoid pool depletion on reconnect failures
            await self._available.put(conn)
    
    async def execute(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        """
        Execute a single command using a pooled connection.
        Retries once with a fresh connection if the pooled connection was stale
        (MPD closes idle TCP connections without notifying the client).
        
        Args:
            command: MPD protocol command
        
        Returns:
            Dictionary of response key-value pairs
        """
        last_exc: Exception | None = None
        for attempt in (1, 2, 3):
            async with self.get_connection() as conn:
                try:
                    return await conn.send_command(command, timeout=timeout)
                except ConnectionError as exc:
                    last_exc = exc
                    logger.warning(
                        "↻ MPD command retry %d/3 on %s for %s after connection error: %s",
                        attempt,
                        conn.label,
                        _command_preview(command),
                        exc,
                    )
                    await conn.close()
                    if attempt < 3:
                        await asyncio.sleep(0.05 * attempt)
                        continue
        raise ConnectionError(f"MPD command failed after retries: {last_exc}")
    
    async def execute_list(self, command: str, timeout: float | None = None) -> List[Dict[str, str]]:
        """
        Execute a list command and return multiple items.
        Retries once with a fresh connection if the pooled connection was stale.
        
        Args:
            command: MPD protocol command that returns multiple items
        
        Returns:
            List of dictionaries, one per item
        """
        last_exc: Exception | None = None
        for attempt in (1, 2, 3):
            async with self.get_connection() as conn:
                try:
                    return await conn.send_command_list(send_cmd=command, timeout=timeout)
                except ConnectionError as exc:
                    last_exc = exc
                    logger.warning(
                        "↻ MPD list retry %d/3 on %s for %s after connection error: %s",
                        attempt,
                        conn.label,
                        _command_preview(command),
                        exc,
                    )
                    await conn.close()
                    if attempt < 3:
                        await asyncio.sleep(0.05 * attempt)
                        continue
        raise ConnectionError(f"MPD list command failed after retries: {last_exc}")

    async def execute_batch(self, commands: List[str], timeout: float | None = None) -> None:
        """Execute multiple non-query commands using MPD command list mode."""
        last_exc: Exception | None = None
        for attempt in (1, 2, 3):
            async with self.get_connection() as conn:
                try:
                    await conn.send_command_batch(commands, timeout=timeout)
                    return
                except ConnectionError as exc:
                    last_exc = exc
                    logger.warning(
                        "↻ MPD batch retry %d/3 on %s after connection error: %s",
                        attempt,
                        conn.label,
                        exc,
                    )
                    await conn.close()
                    if attempt < 3:
                        await asyncio.sleep(0.05 * attempt)
                        continue
        raise ConnectionError(f"MPD batch command failed after retries: {last_exc}")
