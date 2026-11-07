"""The event bus: one place where the app says what changed, so pages and tools hear it instead of polling.

A run that starts, reports a result, moves to its next step, finishes or is deleted, and a workspace file that is
written (a page, a test case, a journey, a schedule, a monitor's history), each publish an event here. Pages
subscribe to the topics they show and refresh when one arrives (webapp/ui/live.py), rather than recomputing a
signature of everything every few seconds; the API streams the same events (GET /api/events) so external tools
can follow along.

Events are small dicts {seq, topic, at, ...data}. `seq` only grows, so "anything new since N?" is one comparison
(`since`, `wait`), and a subscriber that misses one (a slow page) still sees the latest state on the next. The last
EVENTS_KEPT events are kept for catching up; nothing is written to disk. Subscribers are called on the publishing
thread and must return quickly; one that raises is logged and kept.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

EVENTS_KEPT = 1000
TOPICS = ("run", "workspace", "monitor", "schedule")

Listener = Callable[[dict], None]


def publish_on(owner, topic: str, **data) -> None:
    """Publish on owner's run manager's bus when it has one (services take a RunManager or a test's stand-in)."""
    bus = getattr(owner, "bus", None)
    if bus is not None:
        bus.publish(topic, **data)


class EventBus:
    def __init__(self, kept: int = EVENTS_KEPT):
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._events: deque[dict] = deque(maxlen=kept)
        self._subscribers: dict[int, tuple[frozenset[str] | None, Listener]] = {}
        self._next_id = 0
        self._seq = 0
        self._versions: dict[str, int] = {}

    # ───── publishing ─────

    def publish(self, topic: str, **data) -> dict:
        """Say that something changed; returns the event. Never raises because of a subscriber."""
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "topic": topic, "at": time.time(), **data}
            self._events.append(event)
            self._versions[topic] = self._seq
            listeners = [fn for topics, fn in self._subscribers.values() if topics is None or topic in topics]
            self._changed.notify_all()
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - one broken page must not stop the others hearing
                logger.exception("Event subscriber failed on %s", topic)
        return event

    # ───── listening ─────

    def subscribe(self, listener: Listener, topics: Iterable[str] | None = None) -> Callable[[], None]:
        """Call `listener(event)` for each event of `topics` (all when None). Returns the unsubscribe function."""
        wanted = frozenset(topics) if topics is not None else None
        with self._lock:
            self._next_id += 1
            key = self._next_id
            self._subscribers[key] = (wanted, listener)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(key, None)
        return unsubscribe

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def seq(self) -> int:
        return self._seq

    def version(self, *topics: str) -> int:
        """The seq of the latest event of these topics (all when none given); 0 when there was none."""
        with self._lock:
            if not topics:
                return self._seq
            return max((self._versions.get(t, 0) for t in topics), default=0)

    def since(self, seq: int, topics: Iterable[str] | None = None, limit: int = EVENTS_KEPT) -> list[dict]:
        """The kept events after `seq`, oldest first. A caller further behind than what is kept gets what is kept
        (compare the first seq with seq + 1 to tell)."""
        wanted = frozenset(topics) if topics is not None else None
        with self._lock:
            out = [e for e in self._events if e["seq"] > seq and (wanted is None or e["topic"] in wanted)]
        return out[:limit]

    def wait(self, seq: int, timeout: float) -> bool:
        """Block until an event after `seq` exists, or `timeout` seconds pass. True when there is one."""
        with self._changed:
            return self._changed.wait_for(lambda: self._seq > seq, timeout=timeout)
