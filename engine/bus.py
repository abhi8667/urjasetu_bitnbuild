"""Event bus and ring buffer. Owner: B.

In-process publish/subscribe, synchronous dispatch in subscription order. No
asyncio, no queues, no broker — the tick path is single-threaded and stays that
way (PRD §5.2).

The ring buffer is A's only window into the engine. Nothing in the UI reaches
into engine state; it reads `bus.recent_events()` and renders what it finds.
That one-way rule is what keeps replay working when the engine is not running.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

RING_SIZE = 500

#: The minimum set (PRD §5.2). Publishing an unlisted topic is allowed — this is
#: the floor, not the ceiling — but A subscribes to these by name.
TOPICS = (
    "block_opened",
    "strategy_updated",
    "breach_predicted",
    "bill_lines_posted",
    "order_submitted",
    "market_cleared",
    "breach_detected",
    "reshape_proposed",
    "reshape_applied",
    "fallback_curtailed",
    "ageing_applied",
    "battery_moved",
    "delivery_shortfall",
    "grid_risk_predicted",
    "ai_strategy_updated",
    "block_settled",
)


class Bus:
    def __init__(self, ring_size: int = RING_SIZE):
        self._subs: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self.ring: deque[dict] = deque(maxlen=ring_size)

    def subscribe(self, topic: str, fn: Callable[[dict], None]) -> None:
        self._subs[topic].append(fn)

    def publish(self, topic: str, block: int, agent_id: str,
                payload: dict[str, Any] | None = None) -> dict:
        """Append to the ring, then dispatch synchronously in subscription order.

        The event is appended BEFORE dispatch, so a subscriber that raises still
        leaves the event visible to A — a crash that vanishes from the trace is
        far harder to diagnose than one that does not.
        """
        event = {
            "topic": topic,
            "block": block,
            "agent_id": agent_id,
            "payload": payload or {},
        }
        self.ring.append(event)
        for fn in self._subs[topic]:
            fn(event)
        return event

    def recent_events(self, topic: str | None = None, limit: int | None = None) -> list[dict]:
        """What A reads. Newest last, oldest first — the order they happened."""
        events = [e for e in self.ring if topic is None or e["topic"] == topic]
        return events[-limit:] if limit else events

    def clear(self) -> None:
        self.ring.clear()
