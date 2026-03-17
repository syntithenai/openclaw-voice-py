"""
MPD (Music Player Daemon) TCP client with connection pooling.

Provides low-level MPD protocol communication with:
- Automatic reconnection on connection loss
- Connection pooling for instant command execution
- Proper command parsing and response handling
"""

import asyncio
import logging
from typing import Optional, Dict, List
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class MPDConnection:
    """Single TCP connection to MPD server."""
    
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock()
    
    async def connect(self) -> bool:
        """Establish connection to MPD server."""
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
            logger.info(f"Connected to MPD at {self.host}:{self.port}")
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
    
    async def send_command(self, command: str) -> Dict[str, str]:
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
            if not self.is_connected:
                raise ConnectionError("Not connected to MPD")
            
            try:
                # Send command
                self._writer.write(f"{command}\n".encode('utf-8'))
                await asyncio.wait_for(
                    self._writer.drain(),
                    timeout=self.timeout
                )
                
                # Read response
                response = {}
                while True:
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=self.timeout
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
                
                return response
                
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for MPD response")
                self._connected = False
                raise ConnectionError("MPD command timeout")
            except Exception as e:
                logger.error(f"Error sending MPD command: {e}")
                self._connected = False
                raise
    
    async def send_command_list(self, send_cmd: str = "") -> List[Dict[str, str]]:
        """
        Send command and read response for list commands that return multiple items.
        
        Args:
            send_cmd: MPD protocol command to send (if empty, just reads response)
        
        Returns:
            List of dictionaries, one per item
        """
        async with self._lock:
            if not self.is_connected:
                raise ConnectionError("Not connected to MPD")
            
            try:
                # Send command if provided
                if send_cmd:
                    self._writer.write(f"{send_cmd}\n".encode('utf-8'))
                    await asyncio.wait_for(
                        self._writer.drain(),
                        timeout=self.timeout
                    )
                
                items = []
                current_item = {}
                
                while True:
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=self.timeout
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
                
                return items
                
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for MPD list response")
                self._connected = False
                raise ConnectionError("MPD command timeout")
            except ConnectionError:
                # Connection errors are already handled
                raise
            except Exception as e:
                logger.error(f"Error in MPD list response: {e}")
                self._connected = False
                raise ConnectionError(f"MPD list response error: {e}")


class MPDClientPool:
    """
    Connection pool for MPD clients.
    
    Maintains a pool of connections to MPD for instant command execution.
    Handles automatic reconnection and connection health monitoring.
    """
    
    def __init__(self, host: str, port: int, pool_size: int = 3, timeout: float = 5.0):
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
        
        # Check if connection is still valid, reconnect if needed
        if not conn.is_connected:
            logger.debug("Reconnecting to stale MPD connection...")
            if not await conn.connect():
                # Don't put failed connections back into pool - they're bad
                # This prevents a cascade of reconnection failures
                logger.warning("Failed to reconnect to MPD, connection discarded")
                raise ConnectionError("Failed to reconnect to MPD")
        
        try:
            yield conn
        finally:
            # Return connection to pool
            await self._available.put(conn)
    
    async def execute(self, command: str) -> Dict[str, str]:
        """
        Execute a single command using a pooled connection.
        Retries once with a fresh connection if the pooled connection was stale
        (MPD closes idle TCP connections without notifying the client).
        
        Args:
            command: MPD protocol command
        
        Returns:
            Dictionary of response key-value pairs
        """
        async with self.get_connection() as conn:
            try:
                return await conn.send_command(command)
            except ConnectionError:
                # Stale/half-open connection — reconnect and retry once
                await conn.close()
                await asyncio.sleep(0.1)  # Brief backoff before reconnect
                if not await conn.connect():
                    raise ConnectionError("Failed to reconnect to MPD")
                return await conn.send_command(command)
    
    async def execute_list(self, command: str) -> List[Dict[str, str]]:
        """
        Execute a list command and return multiple items.
        Retries once with a fresh connection if the pooled connection was stale.
        
        Args:
            command: MPD protocol command that returns multiple items
        
        Returns:
            List of dictionaries, one per item
        """
        async with self.get_connection() as conn:
            try:
                return await conn.send_command_list(send_cmd=command)
            except ConnectionError as e:
                # Stale/half-open connection — reconnect and retry once
                logger.debug(f"List command failed ({e}), reconnecting and retrying...")
                await conn.close()
                await asyncio.sleep(0.1)  # Brief backoff before reconnect
                if not await conn.connect():
                    raise ConnectionError("Failed to reconnect to MPD")
                await asyncio.sleep(0.1)  # Brief delay before retry
                return await conn.send_command_list(send_cmd=command)
