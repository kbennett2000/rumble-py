# Thread-safe ring buffer that captures rumble.* log records and fans them
# out to live subscribers (used by the web UI's SSE log-tail).
#
# Two consumer patterns are supported:
#
#   buf.snapshot()  -> list[str]
#       The most recent N records. Used to populate the scrollback when an
#       SSE client first connects.
#
#   queue, sub = buf.subscribe(loop)
#       Returns an asyncio.Queue tied to the supplied event loop, and a
#       handle that buf.unsubscribe(sub) can remove later. The logging
#       thread pushes new records into the queue via call_soon_threadsafe,
#       so the asyncio side can await items without bridging threads
#       manually.

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from dataclasses import dataclass

DEFAULT_CAPACITY = 500


@dataclass
class _Subscription:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


class LogBuffer:
    """Ring buffer of formatted log lines plus a multi-subscriber fanout."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._buffer: collections.deque[str] = collections.deque(maxlen=capacity)
        self._subscribers: list[_Subscription] = []
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        """Append a formatted log line and notify every subscriber.

        Safe to call from any thread; subscribers are notified via
        ``loop.call_soon_threadsafe`` so the asyncio side never sees the
        logging thread directly.
        """
        with self._lock:
            self._buffer.append(line)
            subs = list(self._subscribers)

        # Fan out outside the lock to avoid holding it during call_soon_threadsafe.
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, line)
            except RuntimeError:
                # Loop already closed — drop the dead subscriber so we don't
                # keep paying the per-record cost.
                self._remove_locked_safe(sub)

    def snapshot(self) -> list[str]:
        """Return a copy of every line currently in the buffer."""
        with self._lock:
            return list(self._buffer)

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[asyncio.Queue, _Subscription]:
        """Register a subscriber. Returns ``(queue, subscription_handle)``."""
        queue: asyncio.Queue = asyncio.Queue()
        sub = _Subscription(loop=loop, queue=queue)
        with self._lock:
            self._subscribers.append(sub)
        return queue, sub

    def unsubscribe(self, sub: _Subscription) -> None:
        """Remove a previously-registered subscriber. Idempotent."""
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def _remove_locked_safe(self, sub: _Subscription) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)


class _RumbleOnlyFilter(logging.Filter):
    """Only let records whose logger name starts with ``rumble`` through."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "rumble" or record.name.startswith("rumble.")


class RingBufferHandler(logging.Handler):
    """logging.Handler that emits formatted records into a :class:`LogBuffer`."""

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buffer = buffer
        self.addFilter(_RumbleOnlyFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.add(self.format(record))
        except Exception:
            self.handleError(record)


_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def install_log_capture(buffer: LogBuffer) -> RingBufferHandler:
    """Attach a :class:`RingBufferHandler` to the root logger and return it.

    The handler uses the same format as the stdout handler set up by
    ``__main__.py`` so the web UI's log tail matches what scrolls past in
    the terminal.

    Also sets the ``rumble`` logger's level to DEBUG so records produced
    by ``rumble.*`` loggers actually reach the handler. Without this, the
    default root level of WARNING would silently drop INFO and DEBUG
    records before they ever reach us.
    """
    handler = RingBufferHandler(buffer)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    handler.setLevel(logging.DEBUG)
    logging.getLogger("rumble").setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    return handler


def uninstall_log_capture(handler: RingBufferHandler) -> None:
    """Remove a previously-installed :class:`RingBufferHandler`. Idempotent."""
    logging.getLogger().removeHandler(handler)
    logging.getLogger("rumble").setLevel(logging.NOTSET)
