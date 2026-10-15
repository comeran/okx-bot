import pytest

from src.web.ws import WebSocketManager


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

    await manager.connect(exchange, scope="exchange")
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
    await manager.connect(exchange, scope="exchange")

    event = {"type": "risk_event", "strategy": "alpha", "reason": "blocked"}
    await manager.broadcast(event)

    assert alpha.sent == [event]
    assert beta.sent == []
    assert exchange.sent == []


@pytest.mark.asyncio
async def test_exchange_risk_event_without_scope_reaches_only_exchange_connection():
    manager = WebSocketManager()
    strategy = FakeWebSocket()
    exchange = FakeWebSocket()

    await manager.connect(strategy, scope="strategy", strategy="*")
    await manager.connect(exchange, scope="exchange")

    event = {"type": "risk_event", "strategy": "__exchange__", "reason": "divergence"}
    await manager.broadcast(event)

    assert strategy.sent == []
    assert exchange.sent == [event]
