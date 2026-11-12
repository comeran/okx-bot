"""Interpret adverse balance observations without certifying liability freedom.

Liability fields may overlap and have different account-mode applicability.
Neither absent rows nor zero/empty fields prove complete currency coverage.
Negative cash is a separate shortfall, not evidence of a particular loan.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from src.core.private_facts import MAX_NUMERIC_TEXT_LENGTH, validate_raw_text
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_balance_facts import AccountBalanceFact
from src.ops.spot_preflight import CheckStatus, SpotPreflightCheck

_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_LIABILITY_FIELDS = ("liab", "cross_liab", "iso_liab", "upl_liab", "interest")


@dataclass(frozen=True)
class SpotLiabilityDiagnostic:
    checks: tuple[SpotPreflightCheck, ...]
    liability_free: Literal[False] | None = None
    observed_currency_count: int | None = None
    reported_liability_currency_count: int | None = None
    negative_cash_currency_count: int | None = None
    uninterpretable_liability_field_count: int | None = None
    uninterpretable_cash_field_count: int | None = None


def _decimal(value: object) -> Decimal | None:
    if (not isinstance(value, str) or not 0 < len(value) <= MAX_NUMERIC_TEXT_LENGTH
            or _DECIMAL_TEXT.fullmatch(value) is None):
        return None
    return Decimal(value)


def _time(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def _uid(value: object) -> bool:
    try:
        validate_raw_text(value, "UID")
        return True
    except ValueError:
        return False


def evaluate_spot_liability_diagnostic(
    *, config: AccountConfigFact | None, balance: AccountBalanceFact | None,
    now_ms: int, config_received_at: int | None = None,
    balance_received_at: int | None = None, max_age_ms: int | None = 30_000,
    expected_uid: str | None = None, balance_uid: str | None = None,
) -> SpotLiabilityDiagnostic:
    """Inspect every returned currency only within a fresh UID-bound context."""
    checks: list[SpotPreflightCheck] = []

    def add(code: str, status: CheckStatus, reason: str) -> None:
        checks.append(SpotPreflightCheck(code, status, reason))

    def fresh(fact, receipt):
        return (fact is not None and _time(now_ms) and _time(max_age_ms) and _time(receipt)
                and 0 <= now_ms - receipt <= max_age_ms)

    context_fresh = fresh(config, config_received_at) and fresh(balance, balance_received_at)
    add("liability_evidence_freshness", "pass" if context_fresh else "unknown",
        "Configuration and balance must be within the inclusive local receipt age bound.")
    identity: CheckStatus = "unknown"
    if context_fresh and _uid(expected_uid) and _uid(balance_uid):
        identity = "pass" if config.uid == expected_uid == balance_uid else "fail"
    add("liability_evidence_identity", identity,
        "Fresh configuration and balance must share the expected scoped UID.")
    if identity != "pass":
        add("reported_liabilities", "unknown", "Fresh UID-bound liability evidence is unavailable.")
        add("cash_balance_sign", "unknown", "Fresh UID-bound cash evidence is unavailable.")
        return SpotLiabilityDiagnostic(tuple(checks))

    debt_currencies = negative_cash = unknown_liability = unknown_cash = 0
    for asset in balance.details:
        has_debt = False
        for name in _LIABILITY_FIELDS:
            value = _decimal(getattr(asset, name))
            if value is None or value < 0:
                unknown_liability += 1
            elif value > 0:
                has_debt = True
        debt_currencies += has_debt
        cash = _decimal(asset.cash_bal)
        if cash is None:
            unknown_cash += 1
        elif cash < 0:
            negative_cash += 1

    add("reported_liabilities", "fail" if debt_currencies else "unknown",
        "A returned currency has positive liability or accrued-interest evidence."
        if debt_currencies else
        "Zero, absent or uninterpretable fields do not prove complete liability freedom.")
    cash_status: CheckStatus = ("fail" if negative_cash else "unknown"
                                if unknown_cash or not balance.details else "pass")
    add("cash_balance_sign", cash_status,
        "A returned currency has negative cash; this is a shortfall, not a loan assertion."
        if negative_cash else
        "All returned cash values must be interpretable and nonnegative; no coverage is inferred.")
    return SpotLiabilityDiagnostic(
        tuple(checks), liability_free=False if debt_currencies else None,
        observed_currency_count=len(balance.details),
        reported_liability_currency_count=debt_currencies,
        negative_cash_currency_count=negative_cash,
        uninterpretable_liability_field_count=unknown_liability,
        uninterpretable_cash_field_count=unknown_cash,
    )
