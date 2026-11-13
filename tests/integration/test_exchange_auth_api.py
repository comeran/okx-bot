"""Authentication feedback observes OKX, independently of local WebSockets."""

import asyncio
import json
from copy import deepcopy

import pytest
from ccxt.base.errors import (
    AuthenticationError,
    ExchangeError,
    InvalidNonce,
    NetworkError,
    PermissionDenied,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.exchange import okx_client
from src.web.api import settings


class RawExchange:
    def __init__(self, response=None):
        self.response = response if response is not None else {
            "code": "0", "data": [{"uid": "private-uid-do-not-return"}],
        }
        self.requests = []
        self.closed = 0

    async def private_get_account_config(self, params):
        self.requests.append(dict(params))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def close(self):
        self.closed += 1


@pytest.fixture
def configured(monkeypatch):
    config = AppConfig(exchange=ExchangeConfig(
        api_key="saved-private-key", secret="saved-private-secret",
        passphrase="saved-private-passphrase", demo=True,
    ))
    monkeypatch.setattr(settings, "load_runtime_settings", lambda: config)
    exchange = RawExchange()
    constructions = []

    def create(**kwargs):
        constructions.append(dict(kwargs))
        return exchange

    monkeypatch.setattr(okx_client, "create_okx_client", create)
    app = FastAPI()
    app.include_router(settings.create_router(), prefix="/api/settings")
    return config, exchange, constructions, app


def params(config):
    return {"expected_account_scope_id": build_execution_context(config.exchange)[0][
        "account_scope_id"
    ]}


def probe(configured):
    config, _, _, app = configured
    return TestClient(app).post("/api/settings/exchange-auth", params=params(config))


def test_success_uses_only_saved_credentials_and_returns_bounded_observation(configured, tmp_path):
    config, exchange, constructions, app = configured
    before = deepcopy(config)
    files_before = sorted(tmp_path.rglob("*"))
    response = TestClient(app).post(
        "/api/settings/exchange-auth", params=params(config),
        json={"api_key": "do-not-use-request-key"},
    )
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {
        "account_scope_id", "environment", "status", "reason", "provider_code", "checked_at",
    }
    assert result["account_scope_id"] == params(config)["expected_account_scope_id"]
    assert result["environment"] == "sandbox"
    assert result["status"] == "authenticated"
    assert result["reason"] is None and result["provider_code"] is None
    assert type(result["checked_at"]) is int and result["checked_at"] > 0
    assert constructions == [{
        "api_key": config.exchange.api_key, "secret": config.exchange.secret,
        "passphrase": config.exchange.passphrase, "default_type": "spot", "demo": True,
    }]
    assert exchange.requests == [{}] and exchange.closed == 1
    assert "private-uid" not in response.text and "saved-private" not in response.text
    assert config == before and sorted(tmp_path.rglob("*")) == files_before
    context, _ = build_execution_context(config.exchange)
    assert context["identity_verified"] is context["execution_ready"] is False


@pytest.mark.parametrize("auto_loan,spot_borrow", [(False, False), (True, True)])
def test_success_accepts_boolean_configuration_flags_without_projecting_facts(
    configured, auto_loan, spot_borrow,
):
    config, exchange, _, _ = configured
    exchange.response = {
        "code": "0", "data": [{
            "uid": "private-uid-do-not-return", "autoLoan": auto_loan,
            "enableSpotBorrow": spot_borrow, "acctLv": "1", "posMode": "net_mode",
        }],
    }
    response = probe(configured)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "authenticated"
    assert result["reason"] is None and result["provider_code"] is None
    assert "private-uid" not in response.text
    assert "autoLoan" not in response.text and "enableSpotBorrow" not in response.text
    assert exchange.requests == [{}] and exchange.closed == 1
    context, _ = build_execution_context(config.exchange)
    assert context["identity_verified"] is context["execution_ready"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("api_key", "", "credentials_unavailable"),
    ("secret", "", "credentials_unavailable"),
    ("passphrase", " ", "credentials_unavailable"),
    ("market_type", "swap", "unsupported_market"),
])
def test_unconfigured_never_constructs_provider(configured, field, value, reason):
    config, exchange, constructions, _ = configured
    setattr(config.exchange, field, value)
    response = probe(configured)
    assert response.status_code == 200
    assert response.json()["status"] == "unconfigured"
    assert response.json()["reason"] == reason
    assert response.json()["account_scope_id"] is None
    assert constructions == exchange.requests == [] and exchange.closed == 0


@pytest.mark.parametrize("expected", [None, "foreign-scope"])
def test_configured_probe_requires_current_scope_before_provider(configured, expected):
    _, exchange, constructions, app = configured
    response = TestClient(app).post("/api/settings/exchange-auth", params={
        "expected_account_scope_id": expected,
    } if expected is not None else {})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_scope_changed"
    assert constructions == exchange.requests == []


@pytest.mark.parametrize("error,status,reason,code", [
    (ExchangeError('okx {"code":"50119","msg":"saved-private-secret"}'),
     "failed", "invalid_credentials", "50119"),
    (AuthenticationError('okx {"code":"50101","msg":"saved-private-secret"}'),
     "failed", "environment_mismatch", "50101"),
    (AuthenticationError('okx {"code":"50105","msg":"saved-private-secret"}'),
     "failed", "invalid_credentials", "50105"),
    (PermissionDenied('okx {"code":"50120","msg":"saved-private-secret"}'),
     "failed", "permission_denied", "50120"),
    (InvalidNonce('okx {"code":"50102","msg":"saved-private-secret"}'),
     "failed", "timestamp_invalid", "50102"),
    (AuthenticationError("saved-private-secret"), "failed", "authentication_failed", None),
    (NetworkError("saved-private-secret"), "unavailable", "provider_unavailable", None),
    (RuntimeError("saved-private-secret"), "unavailable", "provider_unavailable", None),
])
def test_provider_failures_are_one_shot_sanitized_and_closed(
    configured, caplog, error, status, reason, code,
):
    _, exchange, _, _ = configured
    exchange.response = error
    response = probe(configured)
    assert response.status_code == 200
    result = response.json()
    assert (result["status"], result["reason"], result["provider_code"]) == (status, reason, code)
    assert exchange.requests == [{}] and exchange.closed == 1
    assert "saved-private-secret" not in response.text + caplog.text


@pytest.mark.parametrize("raw,reason,provider_code", [
    ({"code": "50119", "msg": "saved-private-secret", "data": []},
     "invalid_credentials", "50119"),
    ({"code": "0", "data": []}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": None}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": ""}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": " "}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": " uid"}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": "u" * 257}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": 123}]}, "malformed_response", None),
    ({"code": "0", "data": [{}]}, "malformed_response", None),
    ({"code": "0", "data": [{"uid": "1"}, {"uid": "2"}]}, "malformed_response", None),
    ([{"uid": "1"}], "malformed_response", None),
])
def test_raw_errors_and_malformed_success_never_authenticate(
    configured, raw, reason, provider_code,
):
    _, exchange, _, _ = configured
    exchange.response = raw
    response = probe(configured)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == ("failed" if provider_code else "unavailable")
    assert result["reason"] == reason and result["provider_code"] == provider_code
    assert exchange.closed == 1 and len(exchange.requests) == 1
    assert "saved-private-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("api_key", "replacement-key"), ("secret", "replacement-secret"),
    ("passphrase", "replacement-passphrase"), ("demo", False), ("market_type", "swap"),
])
async def test_inflight_credential_or_environment_change_rejects_old_result(
    configured, field, value,
):
    config, exchange, _, app = configured
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(params):
        exchange.requests.append(dict(params))
        started.set()
        await release.wait()
        return exchange.response

    exchange.private_get_account_config = delayed
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post(
            "/api/settings/exchange-auth", params=params(config),
        ))
        await asyncio.wait_for(started.wait(), 2)
        setattr(config.exchange, field, value)
        release.set()
        response = await pending
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_scope_changed"
    assert "private" not in response.text and exchange.closed == 1


@pytest.mark.asyncio
async def test_request_cancellation_closes_transport_and_propagates(configured):
    config, exchange, _, app = configured
    started = asyncio.Event()

    async def delayed(params):
        exchange.requests.append(dict(params))
        started.set()
        await asyncio.Event().wait()

    exchange.private_get_account_config = delayed
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post(
            "/api/settings/exchange-auth", params=params(config),
        ))
        await asyncio.wait_for(started.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert exchange.closed == 1 and exchange.requests == [{}]


def test_request_timeout_is_unavailable_and_closes_transport(configured, monkeypatch):
    from src.web import exchange_auth

    _, exchange, _, _ = configured
    cancelled = []

    async def delayed(params):
        exchange.requests.append(dict(params))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(exchange_auth, "REQUEST_TIMEOUT_SECONDS", 0.01)
    exchange.private_get_account_config = delayed
    response = probe(configured)
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "provider_unavailable"
    assert exchange.closed == 1 and cancelled == [True]


def test_close_error_cannot_leak_credentials(configured, caplog):
    _, exchange, _, _ = configured

    async def close():
        exchange.closed += 1
        raise RuntimeError("saved-private-secret")

    exchange.close = close
    response = probe(configured)
    assert response.status_code == 200
    assert response.json()["status"] == "authenticated"
    assert exchange.closed == 1
    assert "saved-private-secret" not in response.text + caplog.text


def test_credential_text_cannot_invent_provider_code(configured):
    _, exchange, _, _ = configured
    exchange.response = AuthenticationError("bad key saved-50119-private-secret")
    result = probe(configured).json()
    assert result["provider_code"] is None
    assert result["reason"] == "authentication_failed"
    assert "saved" not in json.dumps(result)


@pytest.mark.parametrize("response", [
    ExchangeError('okx {"code":"98765","msg":"saved-private-secret 50119"}'),
    {"code": "98765", "msg": "saved-private-secret 50119", "data": []},
    ExchangeError('okx {"code":"98765","msg":"saved-private-secret code: 50119"}'),
])
def test_unknown_provider_code_is_not_returned_or_mapped_from_message(configured, response):
    _, exchange, _, _ = configured
    exchange.response = response
    result = probe(configured).json()
    assert result["provider_code"] is None
    assert result["status"] == "unavailable" and result["reason"] == "provider_unavailable"
    assert "98765" not in json.dumps(result) and "50119" not in json.dumps(result)


def test_full_app_protects_auth_probe_with_api_token(configured, monkeypatch):
    from src.web.app import create_app

    config, exchange, constructions, _ = configured
    monkeypatch.setenv("OKX_BOT_API_TOKEN", "test-api-token")
    with TestClient(create_app()) as client:
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = client.post(
                "/api/settings/exchange-auth", params=params(config), headers=headers,
            )
            assert response.status_code == 401
            assert exchange.requests == constructions == []
        response = client.post(
            "/api/settings/exchange-auth", params=params(config),
            headers={"Authorization": "Bearer test-api-token"},
        )
    assert response.status_code == 200 and response.json()["status"] == "authenticated"
    assert exchange.requests == [{}] and exchange.closed == 1


@pytest.mark.parametrize("response", [
    {"code": "0", "data": [{"uid": "private-uid"}]},
    ExchangeError('okx {"code":"50119","msg":"saved-private-secret"}'),
])
def test_success_and_failure_do_not_save_settings_or_touch_files(
    configured, monkeypatch, tmp_path, response,
):
    config, exchange, _, _ = configured
    exchange.response = response
    before = deepcopy(config)
    (tmp_path / "bot.db").write_bytes(b"existing-account-facts")
    (tmp_path / "settings.local.yaml").write_text("existing-settings")
    files_before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    def forbidden_save(*args):
        pytest.fail("An authentication observation must not save settings")

    monkeypatch.setattr(settings, "save_runtime_settings", forbidden_save)
    result = probe(configured)
    assert result.status_code == 200
    assert result.json()["status"] in {"authenticated", "failed"}
    assert config == before
    assert {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == files_before


def test_cleanup_wait_is_bounded_and_redacted(configured, monkeypatch):
    from src.web import exchange_auth

    _, exchange, _, _ = configured
    cancelled = []

    async def close():
        exchange.closed += 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    exchange.close = close
    monkeypatch.setattr(exchange_auth, "CLOSE_TIMEOUT_SECONDS", 0.01)
    result = probe(configured)
    assert result.status_code == 200
    assert result.json()["status"] == "authenticated"
    assert exchange.closed == 1 and cancelled == [True]


@pytest.mark.asyncio
async def test_changes_while_closing_also_reject_old_result(configured):
    config, exchange, _, app = configured
    closing, release = asyncio.Event(), asyncio.Event()

    async def close():
        exchange.closed += 1
        closing.set()
        await release.wait()

    exchange.close = close
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post(
            "/api/settings/exchange-auth", params=params(config),
        ))
        await asyncio.wait_for(closing.wait(), 2)
        config.exchange.secret = "new-private-secret"
        release.set()
        result = await pending
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "account_scope_changed"
    assert "private" not in result.text
