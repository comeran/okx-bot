from unittest.mock import AsyncMock, patch

from src.notify.telegram import TelegramNotifier


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
