from src.strategy.yaml_strategy import YAMLStrategy, parse_condition


def test_parse_simple_condition() -> None:
    values = {"fast_ma": 50500, "slow_ma": 50000, "rsi": 65}

    assert parse_condition("fast_ma > slow_ma", values) is True
    assert parse_condition("fast_ma < slow_ma", values) is False
    assert parse_condition("rsi < 70", values) is True
    assert parse_condition("rsi > 70", values) is False


def test_parse_condition_with_literal() -> None:
    values = {"close": 50500}

    assert parse_condition("close > 50000", values) is True
    assert parse_condition("close < 50000", values) is False


def test_yaml_strategy_creation() -> None:
    config = {
        "name": "MA_Cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "params": {"fast": 10, "slow": 30},
        "indicators": {
            "fast_ma": "sma(close, {{ fast }})",
            "slow_ma": "sma(close, {{ slow }})",
        },
        "conditions": {
            "buy": ["fast_ma > slow_ma"],
            "sell": ["fast_ma < slow_ma"],
        },
    }

    strategy = YAMLStrategy(config)

    assert strategy.name == "MA_Cross"
    assert strategy.symbol == "BTC-USDT"
