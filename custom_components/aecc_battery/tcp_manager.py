"""Persistent TCP connection manager for AECC battery devices."""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)


class TCPClientManager:
    """Manages a single persistent TCP connection per (host, port) pair.

    Uses a class-level registry so that multiple callers sharing the same
    host/port always reuse the same underlying socket.
    """

    _connections: dict[tuple[str, int], TCPClientManager] = {}
    _RECONNECT_BASE_DELAY = 2.0
    _RECONNECT_MAX_DELAY = 30.0

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._connect_failures = 0

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, host: str, port: int, timeout: float = 5.0) -> TCPClientManager:
        key = (host, port)
        if key not in cls._connections:
            cls._connections[key] = TCPClientManager(host, port, timeout)
        return cls._connections[key]

    @classmethod
    def remove_instance(cls, host: str, port: int) -> None:
        cls._connections.pop((host, port), None)

    # ── Connection helpers ────────────────────────────────────────────────────

    async def get_reader_writer(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        async with self._lock:
            if not self.writer or self.writer.is_closing():
                await self._connect_locked()
            assert self.reader is not None and self.writer is not None
            return self.reader, self.writer

    async def _connect_locked(self) -> None:
        """Open a fresh socket. The caller must hold ``_lock``."""
        await self._close_locked()
        if self._connect_failures:
            delay = min(
                self._RECONNECT_BASE_DELAY * (2 ** (self._connect_failures - 1)),
                self._RECONNECT_MAX_DELAY,
            )
            _LOGGER.debug(
                "Waiting %.1fs before reconnecting to %s:%s (failure %d)",
                delay,
                self.host,
                self.port,
                self._connect_failures,
            )
            await asyncio.sleep(delay)
        try:
            _LOGGER.info("Connecting to %s:%s (timeout=%ss)", self.host, self.port, self.timeout)
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            self._connect_failures = 0
            _LOGGER.info("Connected to %s:%s", self.host, self.port)
        except TimeoutError:
            self.reader = None
            self.writer = None
            self._connect_failures += 1
            _LOGGER.error("Connection timed out: %s:%s", self.host, self.port)
            raise
        except OSError as exc:
            self.reader = None
            self.writer = None
            self._connect_failures += 1
            _LOGGER.error("Connection failed: %s:%s – %s", self.host, self.port, exc)
            raise

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        """Close the current socket. The caller must hold ``_lock``."""
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (OSError, RuntimeError):
                pass
            _LOGGER.info("Closed connection to %s:%s", self.host, self.port)
        self.reader = None
        self.writer = None

    async def reconnect(self) -> None:
        _LOGGER.info("Reconnecting to %s:%s", self.host, self.port)
        async with self._lock:
            await self._connect_locked()
