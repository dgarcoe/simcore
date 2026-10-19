"""Event bus — pub/sub system for simulation events."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger("simcore.events")


class EventType(Enum):
    TICK_START = "tick_start"
    TICK_END = "tick_end"
    AGENT_ACTION = "agent_action"
    AGENT_SPEAK = "agent_speak"
    AGENT_MOVE = "agent_move"
    AGENT_POST = "agent_post"
    INTERACTION = "interaction"
    ENVIRONMENT_CHANGE = "environment_change"
    SCHEDULED_EVENT = "scheduled_event"
    INJECTED_EVENT = "injected_event"
    SIMULATION_START = "simulation_start"
    SIMULATION_END = "simulation_end"
    REFLECTION = "reflection"
    AGENT_SPAWN = "agent_spawn"
    AGENT_DEATH = "agent_death"
    AGENT_DEPART = "agent_depart"
    ITEM_TRANSFER = "item_transfer"
    TRADE_OFFER = "trade_offer"
    TRADE_RESULT = "trade_result"
    CONDITION_CHANGE = "condition_change"
    NEED_CHANGE = "need_change"
    PLACE_CHANGE = "place_change"
    RULE_FIRED = "rule_fired"
    AGENT_PLAN = "agent_plan"
    BELIEF_SHARED = "belief_shared"
    SPEECH_IGNORED = "speech_ignored"
    AGENT_SEARCH = "agent_search"


@dataclass
class SimEvent:
    """A simulation event."""

    type: EventType
    tick: int
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "tick": self.tick,
            "data": self.data,
            "source": self.source,
        }


# Type for event handlers
EventHandler = Callable[[SimEvent], Coroutine[Any, Any, None] | None]


class EventBus:
    """Pub/sub event system for the simulation."""

    def __init__(self):
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._global_handlers: list[EventHandler] = []
        self._event_log: list[SimEvent] = []
        self._log_limit = 10000

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe to a specific event type."""
        self._handlers[event_type].append(handler)

    def on_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._global_handlers.append(handler)

    async def emit(self, event: SimEvent) -> None:
        """Emit an event to all subscribers."""
        self._event_log.append(event)
        if len(self._event_log) > self._log_limit:
            self._event_log = self._event_log[-self._log_limit:]

        handlers = self._handlers.get(event.type, []) + self._global_handlers
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Event handler error for {event.type}: {e}")

    def get_log(self, last_n: int = 100) -> list[dict]:
        """Get recent events from the log."""
        return [e.to_dict() for e in self._event_log[-last_n:]]

    def clear(self) -> None:
        self._handlers.clear()
        self._global_handlers.clear()
        self._event_log.clear()
