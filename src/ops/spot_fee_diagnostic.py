"""Exact unrounded fee scenarios; no reserve, funds or execution authority.

Rates use OKX's provider sign convention: positive rebate, negative commission.
Only the exact instrument feeGroup and normal maker/taker policy are selected.
Price/quantity increments do not establish fee rounding precision. Rounding and
coverage therefore remain unknown, regardless of the scenario amounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

from src.core.private_facts import MAX_NUMERIC_TEXT_LENGTH, canonical_spot_symbol, validate_raw_text
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_instrument_facts import SpotInstrumentFact
from src.exchange.okx_trade_fee_facts import SpotTradeFeeFact
from src.ops.spot_preflight import CheckStatus, SpotPreflightCheck

_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


@dataclass(frozen=True)
class SpotFeeDiagnostic:
    checks: tuple[SpotPreflightCheck, ...]
    group_id: str | None = None
    currency: str | None = None
    maker_rate: str | None = None
    taker_rate: str | None = None
    maker_signed_amount: str | None = None
    taker_signed_amount: str | None = None
    worst_commission_unrounded: str | None = None


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


def _text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def evaluate_spot_fee_diagnostic(
    *, config: AccountConfigFact | None, instrument: SpotInstrumentFact | None,
    fee: SpotTradeFeeFact | None, symbol: str, side: str, amount: str, price: str,
    now_ms: int, config_received_at: int | None = None,
    instrument_received_at: int | None = None, fee_received_at: int | None = None,
    max_age_ms: int | None = 30_000, expected_uid: str | None = None,
    fee_uid: str | None = None,
) -> SpotFeeDiagnostic:
    checks: list[SpotPreflightCheck] = []

    def add(code: str, status: CheckStatus, reason: str) -> bool:
        checks.append(SpotPreflightCheck(code, status, reason))
        return status == "pass"

    policy = _time(now_ms) and _time(max_age_ms)

    def fresh(fact, received_at):
        return (fact is not None and policy and _time(received_at)
                and 0 <= now_ms - received_at <= max_age_ms)

    fee_fresh = add("trade_fee_freshness", "pass" if fresh(fee, fee_received_at) else "unknown",
                    "A fee observation within the inclusive local receipt age bound is required.")
    try:
        canonical_spot_symbol(symbol)
        native_symbol = True
    except ValueError:
        native_symbol = False
    identity: CheckStatus = "unknown"
    if (native_symbol and fee_fresh and fresh(config, config_received_at)
            and fresh(instrument, instrument_received_at) and _uid(expected_uid) and _uid(fee_uid)):
        identity = "pass" if (
            config.uid == expected_uid == fee_uid
            and fee.requested_inst_id == instrument.inst_id == symbol
        ) else "fail"
    bound = add("fee_identity", identity,
                "Fresh configuration, instrument and request-bound fee evidence "
                "must share UID/symbol.")

    rule: CheckStatus = "unknown"
    if bound:
        rule_types = (instrument.rule_type, fee.rule_type)
        if rule_types == ("normal", "normal"):
            rule = "pass"
        elif any(isinstance(value, str) and value and value != "normal" for value in rule_types):
            rule = "fail"
    normal = add("fee_rule_type", rule,
                 "Only explicit normal instrument and fee rules are modeled.")

    selected = None
    group_status: CheckStatus = "unknown"
    if bound and normal and isinstance(instrument.group_id, str) and instrument.group_id:
        if isinstance(fee.fee_group, tuple) and fee.fee_group:
            selected = next((group for group in fee.fee_group
                             if group.group_id == instrument.group_id), None)
            group_status = "pass" if selected is not None else "fail"
    add("fee_group_mapping", group_status,
        "An exact SPOT instrument groupId match is required; "
        "root/category rates are not substituted.")

    maker = _decimal(selected.maker) if selected is not None else None
    taker = _decimal(selected.taker) if selected is not None else None
    # Comparisons do not round; abs() can apply ambient Decimal precision.
    valid_rates = (maker is not None and taker is not None
                   and Decimal(-1) < maker < Decimal(1) and Decimal(-1) < taker < Decimal(1))
    add("fee_rate_values", "pass" if valid_rates else "unknown",
        "Both ordinary signed rates must be bounded plain decimals with magnitude below one.")

    currency = None
    if bound and normal and side in ("buy", "sell"):
        if config.fee_type == "0":
            currency = instrument.base_ccy if side == "buy" else instrument.quote_ccy
        elif config.fee_type == "1":
            currency = instrument.quote_ccy
    add("fee_currency", "pass" if currency is not None else "unknown",
        "Fee type must explicitly identify the received or quote charging currency.")

    quantity, limit_price = _decimal(amount), _decimal(price)
    amounts = None
    if (valid_rates and currency is not None and quantity is not None and quantity > 0
            and limit_price is not None and limit_price > 0):
        with localcontext(Context(prec=4 * MAX_NUMERIC_TEXT_LENGTH + 16)):
            basis = (quantity if config.fee_type == "0" and side == "buy"
                     else quantity * limit_price)
            maker_amount, taker_amount = basis * maker, basis * taker
            worst = max(Decimal(0), -maker_amount, -taker_amount)
            amounts = (_text(maker_amount), _text(taker_amount), _text(worst))
    add("fee_scenarios", "pass" if amounts is not None else "unknown",
        "Exact unrounded maker/taker scenarios require matching rates, currency "
        "and valid order inputs.")
    add("fee_rounding", "unknown",
        "Trusted trade-fee rounding and promotion coverage are unavailable; "
        "scenarios prove no reserve.")
    return SpotFeeDiagnostic(
        tuple(checks), group_id=selected.group_id if selected is not None else None,
        currency=currency,
        maker_rate=selected.maker if valid_rates else None,
        taker_rate=selected.taker if valid_rates else None,
        maker_signed_amount=amounts[0] if amounts is not None else None,
        taker_signed_amount=amounts[1] if amounts is not None else None,
        worst_commission_unrounded=amounts[2] if amounts is not None else None,
    )
