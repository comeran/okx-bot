import pytest


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("BTC-USDT", "spot"),
        (" btc/usdt ", "spot"),
        ("BTC-USDT-SWAP", "swap"),
        ("BTC/USD:BTC", "swap"),
        ("BTC-USDT-271231", "future"),
        ("BTC/USDT:USDT-271231", "future"),
        ("BTC-USD-271231-100000-C", "option"),
        ("BTC/USD:BTC-271231-100000-P", "option"),
    ],
)
def test_instrument_market_uses_the_complete_instrument_identity(symbol, expected):
    from src.exchange.markets import market_type_for_symbol

    assert market_type_for_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", [None, 123, "", "BTC", "BTC-USDT-unknown", "BTC/USDT:x-y"])
def test_invalid_instrument_does_not_default_to_spot(symbol):
    from src.exchange.markets import market_type_for_symbol

    with pytest.raises(ValueError):
        market_type_for_symbol(symbol)
