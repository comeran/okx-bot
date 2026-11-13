import asyncio

import pytest

from src.web.ws import WebSocketManager

CONTEXT = {"account_scope_id": "account-a", "environment": "sandbox"}


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_manager_rejects_connections_without_an_explicit_scope():
    manager = WebSocketManager()
    unscoped = FakeWebSocket()

    with pytest.raises(ValueError, match="explicit websocket scope"):
        await manager.connect(unscoped)

    assert unscoped.accepted is False
    assert manager.connections == []


@pytest.mark.asyncio
async def test_broadcast_without_scope_reaches_no_scoped_connections():
    manager = WebSocketManager()
    exchange = FakeWebSocket()
    strategy = FakeWebSocket()

    await manager.connect(exchange, scope="exchange", context=CONTEXT)
    await manager.connect(strategy, scope="strategy", strategy="alpha")

    await manager.broadcast({"type": "account"})

    assert exchange.sent == []
    assert strategy.sent == []


@pytest.mark.asyncio
async def test_strategy_broadcast_reaches_only_matching_strategy_connection():
    manager = WebSocketManager()
    alpha = FakeWebSocket()
    beta = FakeWebSocket()

    await manager.connect(alpha, scope="strategy", strategy="alpha")
    await manager.connect(beta, scope="strategy", strategy="beta")

    await manager.broadcast({"type": "strategy_status", "scope": "strategy", "strategy": "alpha"})

    assert alpha.sent == [{"type": "strategy_status", "scope": "strategy", "strategy": "alpha"}]
    assert beta.sent == []


@pytest.mark.asyncio
async def test_strategy_wildcard_receives_all_strategy_events_but_no_exchange_events():
    manager = WebSocketManager()
    wildcard = FakeWebSocket()

    await manager.connect(wildcard, scope="strategy", strategy="*")

    strategy_event = {"type": "strategy_status", "scope": "strategy", "strategy": "alpha"}
    await manager.broadcast(strategy_event)
    await manager.broadcast({"type": "snapshot", "scope": "exchange", "data": {"account": {}}})

    assert wildcard.sent == [strategy_event]


@pytest.mark.asyncio
async def test_strategy_connection_requires_a_strategy_selector():
    manager = WebSocketManager()
    strategy = FakeWebSocket()

    with pytest.raises(ValueError, match="requires strategy"):
        await manager.connect(strategy, scope="strategy")

    assert strategy.sent == []
    assert strategy not in manager.connections


@pytest.mark.asyncio
async def test_strategy_scoped_risk_event_is_filtered_by_strategy_even_without_scope_field():
    manager = WebSocketManager()
    alpha = FakeWebSocket()
    beta = FakeWebSocket()
    exchange = FakeWebSocket()

    await manager.connect(alpha, scope="strategy", strategy="alpha")
    await manager.connect(beta, scope="strategy", strategy="beta")
    await manager.connect(exchange, scope="exchange", context=CONTEXT)

    event = {"type": "risk_event", "strategy": "alpha", "reason": "blocked"}
    await manager.broadcast(event)

    assert alpha.sent == [event]
    assert beta.sent == []
    assert exchange.sent == []


@pytest.mark.asyncio
async def test_broadcast_tolerates_connections_changing_mid_iteration():
    manager = WebSocketManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    late = FakeWebSocket()
    await manager.connect(first, scope="exchange", context=CONTEXT)
    await manager.connect(second, scope="exchange", context=CONTEXT)

    original_send = first.send_json

    async def mutating_send(payload):
        manager.disconnect(second)
        await manager.connect(late, scope="exchange", context=CONTEXT)
        await original_send(payload)

    first.send_json = mutating_send

    event = {"type": "snapshot", "scope": "exchange", "context": CONTEXT}
    await manager.broadcast(event)

    assert first.sent == [event]
    assert late.sent == []


@pytest.mark.asyncio
async def test_unbound_exchange_risk_event_reaches_no_connection():
    manager = WebSocketManager()
    strategy = FakeWebSocket()
    exchange = FakeWebSocket()

    await manager.connect(strategy, scope="strategy", strategy="*")
    await manager.connect(exchange, scope="exchange", context=CONTEXT)

    event = {"type": "risk_event", "strategy": "__exchange__", "reason": "divergence"}
    await manager.broadcast(event)

    assert strategy.sent == []
    assert exchange.sent == []


@pytest.mark.asyncio
async def test_exchange_connections_require_account_context():
    manager = WebSocketManager()
    with pytest.raises(ValueError, match="account context"):
        await manager.connect(FakeWebSocket(), scope="exchange")


@pytest.mark.asyncio
async def test_exchange_financial_events_require_exact_account_and_environment():
    manager = WebSocketManager()
    connection = FakeWebSocket()
    await manager.connect(connection, scope="exchange", context=CONTEXT)
    event = {"type": "risk_event", "scope": "exchange", "strategy": "__exchange__"}
    for context in (None, {**CONTEXT, "environment": "live"},
                    {**CONTEXT, "account_scope_id": "account-b"}):
        await manager.broadcast({**event, "context": context})
    assert connection.sent == []
    await manager.broadcast({**event, "context": CONTEXT})
    assert connection.sent == [{**event, "context": CONTEXT}]


@pytest.mark.asyncio
async def test_context_change_retires_binding_even_when_same_account_returns():
    manager = WebSocketManager()
    old = FakeWebSocket()
    await manager.connect(old, scope="exchange", context=CONTEXT)
    await manager.broadcast({"type": "context_changed"})
    new = FakeWebSocket()
    await manager.connect(new, scope="exchange", context=CONTEXT)
    event = {"type": "account", "scope": "exchange", "context": CONTEXT}
    await manager.broadcast(event)
    assert old.sent == [{"type": "context_changed"}]
    assert new.sent == [event]
    assert old not in manager.connections


@pytest.mark.asyncio
async def test_context_change_reaches_strategy_socket_without_retiring_it():
    manager = WebSocketManager()
    strategy = FakeWebSocket()
    await manager.connect(strategy, scope="strategy", strategy="*")
    await manager.broadcast({"type": "context_changed"})
    assert strategy.sent == [{"type": "context_changed"}]
    assert strategy in manager.connections


@pytest.mark.asyncio
async def test_strategy_socket_rejects_explicit_live_risk_even_through_legacy_branch():
    manager = WebSocketManager()
    strategy = FakeWebSocket()
    await manager.connect(strategy, scope="strategy", strategy="*")
    await manager.broadcast({
        "type": "risk_event", "strategy": "alpha", "runtime_mode": "live",
    })
    assert strategy.sent == []


@pytest.mark.asyncio
async def test_context_change_retires_all_bindings_before_slow_control_send():
    manager = WebSocketManager()
    first, second = FakeWebSocket(), FakeWebSocket()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_send(payload):
        started.set()
        await release.wait()
        first.sent.append(payload)

    first.send_json = slow_send
    await manager.connect(first, scope="exchange", context=CONTEXT)
    await manager.connect(second, scope="exchange", context=CONTEXT)
    changing = asyncio.create_task(manager.broadcast({"type": "context_changed"}))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await manager.broadcast({"type": "account", "scope": "exchange", "context": CONTEXT})
        still_bound = manager.connections
        second_during_change = list(second.sent)
    finally:
        release.set()
        await changing
    assert still_bound == []
    assert second_during_change == []


@pytest.mark.asyncio
async def test_inflight_financial_broadcast_skips_retired_connections():
    manager = WebSocketManager()
    first, second = FakeWebSocket(), FakeWebSocket()
    financial_started, release_financial = asyncio.Event(), asyncio.Event()
    control_started, release_control = asyncio.Event(), asyncio.Event()

    async def first_send(payload):
        if payload["type"] == "account":
            financial_started.set()
            await release_financial.wait()
        first.sent.append(payload)

    async def second_send(payload):
        if payload["type"] == "context_changed":
            control_started.set()
            await release_control.wait()
        second.sent.append(payload)

    first.send_json, second.send_json = first_send, second_send
    await manager.connect(first, scope="exchange", context=CONTEXT)
    await manager.connect(second, scope="exchange", context=CONTEXT)
    financial = asyncio.create_task(manager.broadcast({
        "type": "account", "scope": "exchange", "context": CONTEXT,
    }))
    await asyncio.wait_for(financial_started.wait(), 1)
    changing = asyncio.create_task(manager.broadcast({"type": "context_changed"}))
    await asyncio.wait_for(control_started.wait(), 1)
    try:
        release_financial.set()
        await financial
        second_during_change = list(second.sent)
    finally:
        release_control.set()
        await changing
    assert second_during_change == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["exchange", "strategy"])
async def test_context_change_during_accept_rejects_stale_handshake(scope):
    manager = WebSocketManager()
    connection = FakeWebSocket()
    accepting, release = asyncio.Event(), asyncio.Event()
    closed = []

    async def suspended_accept():
        accepting.set()
        await release.wait()
        connection.accepted = True

    async def close(**kwargs):
        closed.append(kwargs)

    connection.accept, connection.close = suspended_accept, close
    pending = asyncio.create_task(manager.connect(
        connection, scope=scope, strategy="*" if scope == "strategy" else None,
        context=CONTEXT if scope == "exchange" else None,
    ))
    await asyncio.wait_for(accepting.wait(), 1)
    await manager.broadcast({"type": "context_changed"})
    release.set()
    connected = await pending
    assert connected is False
    assert connection not in manager.connections
    assert connection.sent == [{"type": "context_changed"}]
    assert closed == [{"code": 1000, "reason": "exchange context changed"}]
