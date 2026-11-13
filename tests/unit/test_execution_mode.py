from src.core.execution_mode import account_environment, adapter_uses_sandbox


def test_demo_is_sandbox_and_live_is_not():
    assert account_environment("demo", False) == "sandbox"
    assert account_environment("live", True) == "live"
    assert adapter_uses_sandbox("demo", False) is True
    assert adapter_uses_sandbox("live", True) is False
    assert account_environment("paper", False) == "live"
    assert adapter_uses_sandbox("backtest", True) is True
