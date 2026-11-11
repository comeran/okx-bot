from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(frozen=True)
class _ConnectionScope:
    scope: str
    strategy: str | None = None
    account_scope_id: str | None = None
    environment: str | None = None


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, _ConnectionScope] = {}
        self._context_generation = 0

    @property
    def connections(self) -> list[WebSocket]:
        return list(self._connections)

    async def connect(
        self,
        ws: WebSocket,
        *,
        scope: str | None = None,
        strategy: str | None = None,
        context: dict[str, object] | None = None,
    ) -> bool:
        if scope not in {"exchange", "strategy"}:
            raise ValueError("websocket requires an explicit websocket scope")
        if scope == "strategy" and not strategy:
            raise ValueError("strategy websocket requires strategy")
        account_scope_id = context.get("account_scope_id") if context else None
        environment = context.get("environment") if context else None
        if scope == "exchange" and (
            not isinstance(account_scope_id, str) or not account_scope_id
            or environment not in {"sandbox", "live"}
        ):
            raise ValueError("exchange websocket requires account context")
        generation = self._context_generation
        await ws.accept()
        if generation != self._context_generation:
            try:
                await ws.send_json({"type": "context_changed"})
                await ws.close(code=1000, reason="exchange context changed")
            except Exception:
                pass
            return False
        self._connections[ws] = _ConnectionScope(scope, strategy, account_scope_id, environment)
        return True

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    @staticmethod
    def _matches(connection: _ConnectionScope, data: dict[str, object]) -> bool:
        if connection.scope == "strategy" and data.get("type") in {
            "account", "positions", "orders", "trades", "risk_event",
        } and (data.get("runtime_mode") == "live" or data.get("source") == "live_exchange"):
            return False
        message_scope = data.get("scope")
        if message_scope not in {"exchange", "strategy"}:
            # Existing risk-event producers carry a strategy selector but
            # predate the explicit scope field. Treat those as strategy-scoped
            # without making generic unscoped broadcasts visible.
            if data.get("type") == "risk_event" and isinstance(
                data.get("strategy"), str
            ):
                message_scope = (
                    "exchange"
                    if data.get("strategy") == "__exchange__"
                    else "strategy"
                )
            else:
                return False
        if connection.scope == "exchange":
            context = data.get("context")
            return (
                message_scope == "exchange" and isinstance(context, dict)
                and context.get("account_scope_id") == connection.account_scope_id
                and context.get("environment") == connection.environment
            )
        return (
            message_scope == "strategy"
            and connection.strategy is not None
            and (
                connection.strategy == "*"
                or data.get("strategy") == connection.strategy
            )
        )

    async def broadcast(self, data: dict[str, object]) -> None:
        if data.get("type") == "context_changed":
            self._context_generation += 1
            # Retire the binding even if a later config returns to the same key.
            previous_connections = list(self._connections.items())
            for conn, connection in previous_connections:
                if connection.scope == "exchange":
                    self.disconnect(conn)
            for conn, connection in previous_connections:
                try:
                    await conn.send_json(data)
                    if connection.scope == "exchange":
                        await conn.close(code=1000, reason="exchange context changed")
                except Exception:
                    if self._connections.get(conn) is connection:
                        self.disconnect(conn)
            return
        disconnected = []
        for conn, connection in list(self._connections.items()):
            if self._connections.get(conn) is not connection:
                continue
            if not self._matches(connection, data):
                continue
            try:
                await conn.send_json(data)
            except Exception:
                disconnected.append((conn, connection))
        for conn, connection in disconnected:
            if self._connections.get(conn) is connection:
                self.disconnect(conn)
