from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(frozen=True)
class _ConnectionScope:
    scope: str
    strategy: str | None = None


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, _ConnectionScope] = {}

    @property
    def connections(self) -> list[WebSocket]:
        return list(self._connections)

    async def connect(
        self,
        ws: WebSocket,
        *,
        scope: str | None = None,
        strategy: str | None = None,
    ) -> None:
        if scope not in {"exchange", "strategy"}:
            raise ValueError("websocket requires an explicit websocket scope")
        if scope == "strategy" and not strategy:
            raise ValueError("strategy websocket requires strategy")
        await ws.accept()
        self._connections[ws] = _ConnectionScope(scope, strategy)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    @staticmethod
    def _matches(connection: _ConnectionScope, data: dict[str, object]) -> bool:
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
            return message_scope == "exchange"
        return (
            message_scope == "strategy"
            and connection.strategy is not None
            and (
                connection.strategy == "*"
                or data.get("strategy") == connection.strategy
            )
        )

    async def broadcast(self, data: dict[str, object]) -> None:
        disconnected = []
        for conn, connection in self._connections.items():
            if not self._matches(connection, data):
                continue
            try:
                await conn.send_json(data)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.disconnect(conn)
