from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

import httpx


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, text: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            )

    def format_position_opened(
        self,
        strategy: str,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        stop_loss: float | None = None,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "Position Opened",
            f"Strategy: {strategy}",
            f"Symbol: {symbol}",
            f"Side: {side.title()}",
            f"Amount: {amount}",
            f"Price: {price:,.2f} USDT",
        ]
        if stop_loss:
            lines.append(f"Stop-Loss: {stop_loss:,.2f} USDT")
        lines.append(f"Time: {now}")
        return "\n".join(lines)

    def format_risk_alert(self, alert_type: str, detail: str) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"Risk Alert: {alert_type}\n{detail}\nTime: {now}"

    def format_risk_event(self, payload: dict[str, Any]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fields = [
            ("Strategy", payload.get("strategy")),
            ("Reason", payload.get("reason")),
            ("Reason Code", payload.get("reason_code")),
            ("Symbol", payload.get("symbol")),
            ("Side", payload.get("side")),
            ("Order Type", payload.get("order_type")),
            ("Amount", payload.get("amount")),
            ("Price", payload.get("price")),
            ("Requested Price", payload.get("requested_price")),
            ("Order Value", payload.get("order_value")),
            ("Order ID", payload.get("order_id")),
            ("Event Timestamp", payload.get("timestamp")),
        ]
        lines = ["Risk Event"]
        for label, value in fields:
            if value is None:
                continue
            lines.append(f"{label}: {escape(str(value))}")
        lines.append(f"Notified At: {now}")
        return "\n".join(lines)


class RiskEventTelegramNotifier:
    def __init__(self, notifier: TelegramNotifier) -> None:
        self.notifier = notifier

    async def send_risk_event(self, payload: dict[str, Any]) -> None:
        if payload.get("type") != "risk_event":
            return
        await self.notifier.send(self.notifier.format_risk_event(payload))
