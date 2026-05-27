from __future__ import annotations

import pytest

from src.core.events import Event, EventBus


@pytest.mark.asyncio
async def test_subscribe_emit_receives_bar_event() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("bar", handler)

    await bus.emit(Event(type="bar", data={"close": 50000}))

    assert len(received) == 1
    assert received[0].type == "bar"
    assert received[0].data["close"] == 50000


@pytest.mark.asyncio
async def test_multiple_subscribers_both_receive_order_event() -> None:
    bus = EventBus()
    first_received: list[Event] = []
    second_received: list[Event] = []

    async def first_handler(event: Event) -> None:
        first_received.append(event)

    async def second_handler(event: Event) -> None:
        second_received.append(event)

    bus.subscribe("order", first_handler)
    bus.subscribe("order", second_handler)
    event = Event(type="order", data={"id": "order-1"})

    await bus.emit(event)

    assert first_received == [event]
    assert second_received == [event]


@pytest.mark.asyncio
async def test_unsubscribe_prevents_handler_being_called() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    token = bus.subscribe("bar", handler)
    bus.unsubscribe(token)

    await bus.emit(Event(type="bar", data={"close": 50000}))

    assert received == []


@pytest.mark.asyncio
async def test_emitting_unknown_event_type_does_not_raise() -> None:
    bus = EventBus()

    await bus.emit(Event(type="unknown", data={}))
