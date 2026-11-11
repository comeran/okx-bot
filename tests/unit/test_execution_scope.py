import json
from dataclasses import FrozenInstanceError
from importlib import import_module

import pytest

from src.core.config import ExchangeConfig
from src.data.allocations import AllocationScope


def scope_module():
    return import_module("src.core.execution_scope")


def configured(**overrides):
    return ExchangeConfig(**{
        "api_key": "synthetic-key-A", "secret": "synthetic-secret",
        "passphrase": "synthetic-passphrase", **overrides,
    })


def test_context_is_unverified_and_never_exposes_raw_credentials():
    exchange = configured()
    context, scope = scope_module().build_execution_context(exchange)

    assert context == {
        "account_scope_id": scope.account_scope_id,
        "exchange": "okx", "environment": "sandbox", "market_type": "spot",
        "identity_verified": False, "available": True, "reason": None,
        "execution_ready": False,
    }
    serialized = json.dumps(context)
    for secret in (exchange.api_key, exchange.secret, exchange.passphrase):
        assert secret not in serialized
    assert len(scope.account_scope_id) == 64
    assert set(scope.account_scope_id) <= set("0123456789abcdef")


def test_scope_is_immutable_and_distinguishes_keys_and_environments():
    context, first = scope_module().build_execution_context(configured())
    _, same = scope_module().build_execution_context(configured(secret="changed-secret"))
    _, other = scope_module().build_execution_context(configured(api_key="synthetic-key-B"))
    _, live = scope_module().build_execution_context(configured(demo=False))

    assert first == same
    assert len({first.account_scope_id, other.account_scope_id, live.account_scope_id}) == 3
    assert context["environment"] == "sandbox"
    assert live.environment == "live"
    with pytest.raises(FrozenInstanceError):
        first.environment = "live"


def test_account_identity_excludes_market_type_and_preserves_allocation_scope_contract():
    module = scope_module()
    first = module.ExecutionScope(
        exchange="okx", environment="sandbox", account_fingerprint="a" * 64,
    )
    same = module.ExecutionScope(
        exchange="okx", environment="sandbox", account_fingerprint="a" * 64,
    )
    assert first.account_scope_id == same.account_scope_id
    assert "market_type" not in first.__dataclass_fields__
    allocation = AllocationScope(account_fingerprint="a" * 64, environment="sandbox")
    assert first.account_scope_id != allocation.scope_id


@pytest.mark.parametrize("field", ["api_key", "secret", "passphrase"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_missing_credentials_return_unavailable_context(field, value):
    context, scope = scope_module().build_execution_context(configured(**{field: value}))
    assert scope is None
    assert context["account_scope_id"] is None
    assert context["available"] is False
    assert context["reason"] == "credentials_unavailable"
    assert context["execution_ready"] is False


@pytest.mark.parametrize("market", ["swap", "future", "option", "", None])
def test_unsupported_market_never_creates_execution_scope(market):
    context, scope = scope_module().build_execution_context(configured(market_type=market))
    assert scope is None
    assert context["available"] is False
    assert context["reason"] == "unsupported_market"


def test_context_normalizes_spot_capability_and_key_whitespace():
    _, first = scope_module().build_execution_context(configured())
    context, same = scope_module().build_execution_context(configured(
        api_key=" synthetic-key-A ", market_type=" SPOT ",
    ))
    assert same == first
    assert context["market_type"] == "spot"


@pytest.mark.parametrize("overrides", [
    {"exchange": "other"}, {"environment": "paper"},
    {"account_fingerprint": "raw-key"}, {"account_fingerprint": "A" * 64},
    {"account_fingerprint": None},
])
def test_invalid_scope_cannot_be_used_as_storage_identity(overrides):
    with pytest.raises(ValueError):
        scope_module().ExecutionScope(**{
            "exchange": "okx", "environment": "sandbox",
            "account_fingerprint": "a" * 64, **overrides,
        })
