"""One-shot, read-only authentication observations for saved OKX credentials."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy

from ccxt.base.errors import AuthenticationError, InvalidNonce, PermissionDenied
from fastapi import HTTPException

from src.core.config import AppConfig
from src.core.execution_scope import build_execution_context
from src.core.private_facts import validate_raw_text
from src.exchange import okx_client
from src.exchange.errors import MalformedProviderData

REQUEST_TIMEOUT_SECONDS = 10
CLOSE_TIMEOUT_SECONDS = 2
_PROVIDER_CODE = re.compile(r'"code"\s*:\s*"([0-9]{5})"')
_INVALID_CREDENTIAL_CODES = {"50103", "50104", "50105", "50111", "50113", "50114", "50119"}
_PERMISSION_CODES = {"50110", "50120"}
_TIMESTAMP_CODES = {"50102", "50112"}
_SAFE_PROVIDER_CODES = (
    _INVALID_CREDENTIAL_CODES | _PERMISSION_CODES | _TIMESTAMP_CODES | {"50101"}
)


def _safe_provider_code(value: object) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_PROVIDER_CODES else None


def _scope_changed() -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "account_scope_changed"})


def _validate_auth_success(payload: object) -> None:
    """Observe signed access only; unrelated account facts have separate parsers."""
    if (
        not isinstance(payload, Mapping)
        or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], Mapping)
    ):
        raise MalformedProviderData("invalid OKX authentication success envelope")
    try:
        validate_raw_text(payload["data"][0].get("uid"), "uid")
    except ValueError as error:
        raise MalformedProviderData("invalid OKX authentication identity") from error


def _failure(code: str | None, error: Exception | None = None) -> tuple[str, str]:
    if code == "50101":
        return "failed", "environment_mismatch"
    if code in _INVALID_CREDENTIAL_CODES:
        return "failed", "invalid_credentials"
    if code in _PERMISSION_CODES or isinstance(error, PermissionDenied):
        return "failed", "permission_denied"
    if code in _TIMESTAMP_CODES or isinstance(error, InvalidNonce):
        return "failed", "timestamp_invalid"
    if isinstance(error, AuthenticationError):
        return "failed", "authentication_failed"
    return "unavailable", "provider_unavailable"


async def probe_saved_exchange_auth(
    load_settings: Callable[[], AppConfig], expected_account_scope_id: str | None,
) -> dict[str, object]:
    """Never persist identity or authorize execution based on this observation."""
    exchange = deepcopy(load_settings().exchange)
    context, scope = build_execution_context(exchange)
    result: dict[str, object] = {
        "account_scope_id": context["account_scope_id"],
        "environment": context["environment"],
        "status": "unconfigured",
        "reason": context["reason"],
        "provider_code": None,
        "checked_at": int(time.time() * 1000),
    }
    if scope is None:
        return result
    if expected_account_scope_id != scope.account_scope_id:
        raise _scope_changed()

    client = None
    try:
        client = okx_client.create_okx_client(
            api_key=exchange.api_key, secret=exchange.secret,
            passphrase=exchange.passphrase, default_type=exchange.market_type,
            demo=exchange.demo,
        )
        payload = await asyncio.wait_for(
            client.private_get_account_config({}), timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raw_code = payload.get("code") if isinstance(payload, Mapping) else None
        if isinstance(raw_code, str) and re.fullmatch(r"[0-9]{5}", raw_code):
            code = _safe_provider_code(raw_code)
            result["provider_code"] = code
            result["status"], result["reason"] = _failure(code)
        else:
            _validate_auth_success(payload)
            result.update(status="authenticated", reason=None)
    except MalformedProviderData:
        result.update(status="unavailable", reason="malformed_response")
    except Exception as error:
        # Read only an explicit provider code field, never arbitrary digits or messages.
        match = _PROVIDER_CODE.search(str(error))
        code = _safe_provider_code(match.group(1)) if match else None
        result["provider_code"] = code
        result["status"], result["reason"] = _failure(code, error)
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.close(), timeout=CLOSE_TIMEOUT_SECONDS)
            except Exception:
                # Cleanup must not expose credentials carried by transport exceptions.
                pass

    # The scope fingerprint covers only the key. Compare all saved fields as well,
    # after request and cleanup awaits, to reject same-key secret/passphrase races.
    if load_settings().exchange != exchange:
        raise _scope_changed()
    result["checked_at"] = int(time.time() * 1000)
    return result
