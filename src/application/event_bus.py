from __future__ import annotations

import logging
from typing import Protocol

from src.application.events import DomainEvent

LOG = logging.getLogger(__name__)


class EventSink(Protocol):
    def append_event(self, event: DomainEvent) -> None: ...


class EventBus:
    """In-process event bus with optional persistence sink."""

    def __init__(self, sink: EventSink | None = None):
        self._sink = sink
        self._events: list[DomainEvent] = []

    @property
    def events(self) -> list[DomainEvent]:
        return list(self._events)

    def publish(self, event: DomainEvent) -> None:
        self._events.append(event)
        if self._sink is None:
            return
        try:
            self._sink.append_event(event)
        except Exception as exc:  # pragma: no cover - defensive logging path
            LOG.warning("Failed to persist domain event %s: %s", event.event_type, exc)

