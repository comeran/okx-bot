import pytest

from src.core.execution_mode import account_environment, adapter_uses_sandbox


def test_demo_is_sandbox_and_live_is_not():
    assert account_environment("demo", True) == "sandbox"
    assert account_environment("live", False) == "live"
    assert adapter_uses_sandbox("demo", True) is True
    assert adapter_uses_sandbox("live", False) is False
    assert account_environment("paper", False) == "live"
    assert adapter_uses_sandbox("backtest", True) is True


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
@pytest.mark.parametrize("resolve", [account_environment, adapter_uses_sandbox])
def test_conflicting_environment_is_rejected(mode, demo, resolve):
    with pytest.raises(ValueError, match="exchange.demo"):
        resolve(mode, demo)
