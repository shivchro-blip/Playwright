"""Pages that update when something changes, not on a timer: subscribe a refresh to the event bus.

    live(svc.bus, ["run"], self.table_view.refresh)

calls the refresh on the page's own event loop, inside the page's context, a moment (DEBOUNCE seconds) after the
first of a burst of events: thirty test results arriving at once refresh the page once. `when(event)` narrows the
events that count (e.g. only runs of the system shown). A slow `fallback` timer still refreshes now and then, for
what no event covers (the clock moving a schedule's next run, a file edited outside the app); it is cheap because
it only refreshes when something was published since, unless `always` is set. The subscription ends with the page
(its client closes or the element is removed).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Iterable

from nicegui import background_tasks, context, core, ui

from webapp.services.events import EventBus

logger = logging.getLogger(__name__)

DEBOUNCE = 0.25


class Live:
    def __init__(self, bus: EventBus, topics: Iterable[str], refresh: Callable[[], object], *,
                 when: Callable[[dict], bool] | None = None, fallback: float | None = 30.0, always: bool = False):
        self.bus, self.topics, self.refresh, self.when = bus, tuple(topics), refresh, when
        self.client = context.client
        self.container = context.slot.parent
        self.loop = core.loop or asyncio.get_event_loop()
        self.pending = False
        self.refreshes = 0
        self.seen = bus.version(*self.topics)
        self.unsubscribe = bus.subscribe(self._heard, self.topics)
        self.client.on_disconnect(self._maybe_stop)
        if fallback:
            ui.timer(fallback, lambda: self._fallback(always))

    @property
    def gone(self) -> bool:
        return self.client.is_deleted or self.container.is_deleted

    def stop(self) -> None:
        self.unsubscribe()

    def _maybe_stop(self) -> None:
        if self.gone:
            self.stop()

    def _heard(self, event: dict) -> None:
        """On the publishing thread: hand over to the page's loop."""
        if self.gone:
            self.stop()
            return
        if self.when is not None:
            try:
                if not self.when(event):
                    return
            except Exception:  # noqa: BLE001
                logger.exception("Live filter failed")
                return
        if self.pending:
            return
        self.pending = True
        try:
            self.loop.call_soon_threadsafe(lambda: background_tasks.create(self._later(), name="live-refresh"))
        except RuntimeError:  # the loop has closed (app stopping)
            self.stop()

    async def _later(self) -> None:
        await asyncio.sleep(DEBOUNCE)
        self.pending = False
        self._run()

    def _fallback(self, always: bool) -> None:
        if always or self.bus.version(*self.topics) != self.seen:
            self._run()

    def _run(self) -> None:
        if self.gone:
            self.stop()
            return
        self.seen = self.bus.version(*self.topics)
        self.refreshes += 1
        try:
            with self.container:
                self.refresh()
        except Exception:  # noqa: BLE001 - a page that fails to refresh must keep listening
            logger.exception("Live refresh failed")


def live(bus: EventBus, topics: Iterable[str], refresh: Callable[[], object], **options) -> Live:
    return Live(bus, topics, refresh, **options)
