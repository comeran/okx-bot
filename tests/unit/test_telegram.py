from unittest.mock import AsyncMock, patch

from src.notify.telegram import RiskEventTelegramNotifier, TelegramNotifier


async def test_send_posts_message_to_telegram() -> None:
    mock_client = AsyncMock()

    with patch("httpx.AsyncClient") as async_client:
        async_client.return_value.__aenter__.return_value = mock_client

        notifier = TelegramNotifier(bot_token="test-token", chat_id="123")
        await notifier.send("Test message")

    mock_client.post.assert_awaited_once_with(
        "https://api.telegram.org/bottest-token/sendMessage",
        json={"chat_id": "123", "text": "Test message", "parse_mode": "HTML"},
    )


def test_format_position_opened_includes_strategy_symbol_and_formatted_price() -> None:
    notifier = TelegramNotifier(bot_token="test-token", chat_id="123")

    message = notifier.format_position_opened(
        strategy="MA_Cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=0.1,
        price=50000,
        stop_loss=49000,
    )

    assert "MA_Cross" in message
    assert "BTC-USDT-SWAP" in message
    assert "50,000" in message


def test_format_risk_alert_includes_alert_type_and_percentage_detail() -> None:
    notifier = TelegramNotifier(bot_token="test-token", chat_id="123")

    message = notifier.format_risk_alert(
        alert_type="Daily Loss",
        detail="Loss reached 5.2%, exceeding 5% threshold",
    )

    assert "Daily Loss" in message
    assert "5.2%" in message


def test_format_risk_event_includes_payload_fields_and_escapes_html() -> None:
    notifier = TelegramNotifier(bot_token="test-token", chat_id="123")

    message = notifier.format_risk_event(
        {
            "type": "risk_event",
            "strategy": "MA <Cross>",
            "reason": "Stop <loss> & risk",
            "reason_code": "stop_loss_required",
            "symbol": "BTC-USDT",
            "side": "buy",
            "order_type": "market",
            "amount": 0.1,
            "price": 50000.0,
            "requested_price": None,
            "order_value": 5000.0,
            "order_id": "order-1",
            "timestamp": 1700000000000,
        }
    )

    assert "Risk Event" in message
    assert "Strategy: MA &lt;Cross&gt;" in message
    assert "Reason: Stop &lt;loss&gt; &amp; risk" in message
    assert "Reason Code: stop_loss_required" in message
    assert "Requested Price" not in message


async def test_risk_event_telegram_notifier_sends_formatted_risk_events() -> None:
    telegram = TelegramNotifier(bot_token="test-token", chat_id="123")
    telegram.send = AsyncMock()
    notifier = RiskEventTelegramNotifier(telegram)

    await notifier.send_risk_event(
        {
            "type": "risk_event",
            "strategy": "MA_Cross",
            "reason": "Order requires a stop loss",
            "reason_code": "stop_loss_required",
            "symbol": "BTC-USDT",
        }
    )
    await notifier.send_risk_event({"type": "orders"})

    telegram.send.assert_awaited_once()
    assert "Risk Event" in telegram.send.await_args.args[0]
    assert "MA_Cross" in telegram.send.await_args.args[0]
