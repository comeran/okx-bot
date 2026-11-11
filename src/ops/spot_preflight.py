"""Pure, conservative diagnostics for a native SPOT limit order.

The caller supplies facts from one trusted account/environment, their local
receipt times and current completeness evidence. Balance facts contain no UID,
so the caller must supply their account binding separately. No provider lookup,
storage, funding, reservations, activation or order submission happens here.

Only exact raw ``0``/``0`` borrow flags pass the policy check; they never prove
absence of debt. Liability freedom and complete synchronization/reconciliation
require explicit caller attestations. A nonnegative fee reserve is denominated
in the spending currency (quote for buys, base for sells), and must cover the
caller's fee model. Unknown fee currency/coverage must be supplied as None.

Absent or stale observations are unknown; observed non-live states fail. Receipt
freshness uses an inclusive age bound and rejects future receipts. Readiness
booleans describe only these checks, never authority to activate a strategy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal

from src.core.private_facts import MAX_NUMERIC_TEXT_LENGTH, canonical_spot_symbol
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_balance_facts import AccountBalanceFact
from src.exchange.okx_instrument_facts import SpotInstrumentFact

CheckStatus = Literal["pass", "fail", "unknown"]
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
# Four bounded inputs suffice for exact multiplication, addition and remainder
# across the largest accepted integer/fractional exponent differences. A fresh
# Context isolates results from the caller's precision, traps and rounding mode.
_ARITHMETIC_PRECISION = 4 * MAX_NUMERIC_TEXT_LENGTH + 16


@dataclass(frozen=True)
class SpotPreflightCheck:
    code: str
    status: CheckStatus
    reason: str


@dataclass(frozen=True)
class SpotPreflightResult:
    checks: tuple[SpotPreflightCheck, ...]

    @property
    def can_submit(self) -> bool:
        return bool(self.checks) and all(check.status == "pass" for check in self.checks)

    @property
    def can_activate(self) -> bool:
        """Diagnostic readiness only; no allocation/runtime authorization."""
        return self.can_submit


def _decimal(value: object) -> Decimal | None:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_NUMERIC_TEXT_LENGTH
        or _DECIMAL_TEXT.fullmatch(value) is None
    ):
        return None
    return Decimal(value)


def _timestamp(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def evaluate_spot_order_preflight(
    *,
    config: AccountConfigFact | None,
    balance: AccountBalanceFact | None,
    instrument: SpotInstrumentFact | None,
    symbol: str,
    side: str,
    amount: str,
    price: str,
    now_ms: int,
    config_received_at: int | None = None,
    balance_received_at: int | None = None,
    instrument_received_at: int | None = None,
    max_age_ms: int | None = 30_000,
    expected_uid: str | None = None,
    balance_uid: str | None = None,
    fee_reserve: str | None = None,
    liability_free: bool | None = None,
    reconciliation_complete: bool | None = None,
    sync_complete: bool | None = None,
) -> SpotPreflightResult:
    """Explain every required gate without changing facts or external state.

    ``amount`` is base units, ``price`` quote per base; both are exact bounded
    decimal strings. max_age_ms=None disables freshness proof, not freshness
    enforcement. Bad requests produce failed checks; bad/missing observations
    produce unknown checks, except explicit unsafe observations fail closed.
    """
    checks: list[SpotPreflightCheck] = []

    def add(code: str, status: CheckStatus, reason: str) -> bool:
        checks.append(SpotPreflightCheck(code, status, reason))
        return status == "pass"

    policy_valid = _timestamp(now_ms) and _timestamp(max_age_ms)
    add("freshness_policy", "pass" if policy_valid else "unknown",
        "Bounded current time and maximum observation age supplied."
        if policy_valid else "Freshness requires nonnegative integer time and maximum age.")

    def fresh(name: str, fact: object, received_at: object) -> bool:
        valid = (
            fact is not None and policy_valid and _timestamp(received_at)
            and 0 <= now_ms - received_at <= max_age_ms
        )
        return add(f"{name}_freshness", "pass" if valid else "unknown",
                   "Observation is within the inclusive local receipt age bound."
                   if valid else "Observation or valid fresh local receipt time is unavailable.")

    config_fresh = fresh("config", config, config_received_at)
    balance_fresh = fresh("balance", balance, balance_received_at)
    instrument_fresh = fresh("instrument", instrument, instrument_received_at)

    identity: CheckStatus = "unknown"
    identity_reason = "Fresh configuration, expected UID and balance UID binding are required."
    if config_fresh and config is not None and expected_uid and balance_uid:
        if config.uid == expected_uid == balance_uid:
            identity = "pass"
            identity_reason = "Config UID matches the caller's expected and balance-bound UID."
        else:
            identity = "fail"
            identity_reason = "Configuration UID and caller's account/balance UID binding differ."
    add("identity", identity, identity_reason)

    borrow: CheckStatus = "unknown"
    borrow_reason = "Both fresh raw autoLoan and enableSpotBorrow flags must be exactly '0'."
    if config_fresh and config is not None:
        if "1" in (config.auto_loan, config.enable_spot_borrow):
            borrow, borrow_reason = "fail", "A raw account borrow flag is '1'."
        elif (config.auto_loan, config.enable_spot_borrow) == ("0", "0"):
            borrow = "pass"
            borrow_reason = "Both raw borrow flags are disabled; this proves policy only."
    add("borrow_policy", borrow, borrow_reason)

    try:
        canonical_spot_symbol(symbol)
        symbol_valid = add("symbol", "pass", "Requested symbol is a native BASE-QUOTE spot ID.")
    except ValueError:
        symbol_valid = add("symbol", "fail", "A native BASE-QUOTE spot symbol is required.")
    side_valid = add("side", "pass" if side in ("buy", "sell") else "fail",
                     "Side is an exact spot buy/sell value."
                     if side in ("buy", "sell") else "Side must be exactly 'buy' or 'sell'.")
    quantity, limit_price, reserve = _decimal(amount), _decimal(price), _decimal(fee_reserve)
    amount_valid = add("amount", "pass" if quantity is not None and quantity > 0 else "fail",
                       "Amount is a positive exact base-unit decimal."
                       if quantity is not None and quantity > 0
                       else "Amount must be a bounded positive decimal string.")
    price_valid = add("price", "pass" if limit_price is not None and limit_price > 0 else "fail",
                      "Price is a positive exact quote-per-base decimal."
                      if limit_price is not None and limit_price > 0
                      else "Price must be a bounded positive decimal string.")
    add("fee_reserve", "pass" if reserve is not None and reserve >= 0 else "unknown",
        "Caller supplied a nonnegative exact fee reserve in the spending currency."
        if reserve is not None and reserve >= 0
        else "Fee coverage is unknown; an exact nonnegative spending-currency reserve is required.")

    instrument_matches = False
    if instrument_fresh and instrument is not None and symbol_valid:
        instrument_matches = instrument.inst_id == symbol
        add("instrument_identity", "pass" if instrument_matches else "fail",
            "Instrument matches the requested native symbol." if instrument_matches
            else "Observed instrument differs from the requested native symbol.")
    else:
        add("instrument_identity", "unknown", "Fresh matching instrument evidence is unavailable.")
    state: CheckStatus = "unknown"
    state_reason = "Fresh matching instrument state is unavailable."
    if instrument_matches:
        if instrument.state == "live":
            state, state_reason = "pass", "Observed instrument state is exactly 'live'."
        elif isinstance(instrument.state, str) and instrument.state:
            state, state_reason = "fail", "Observed instrument state is not 'live'."
    add("instrument_state", state, state_reason)

    tick = _decimal(instrument.tick_sz) if instrument_matches else None
    lot = _decimal(instrument.lot_sz) if instrument_matches else None
    minimum = _decimal(instrument.min_sz) if instrument_matches else None
    rules_valid = all(value is not None and value > 0 for value in (tick, lot, minimum))
    add("instrument_rules", "pass" if rules_valid else "unknown",
        "Positive exact tick, lot and minimum-size rules are available." if rules_valid
        else "Fresh positive exact tick, lot and minimum-size rules are required.")

    with localcontext(Context(prec=_ARITHMETIC_PRECISION)):
        for code, available, passed, reason in (
            ("amount_increment", rules_valid and amount_valid,
             rules_valid and amount_valid and quantity % lot == 0,
             "Amount must be an exact multiple of lotSz."),
            ("price_increment", rules_valid and price_valid,
             rules_valid and price_valid and limit_price % tick == 0,
             "Price must be an exact multiple of tickSz."),
            ("minimum_size", rules_valid and amount_valid,
             rules_valid and amount_valid and quantity >= minimum,
             "Amount must be at least minSz."),
        ):
            add(code, "pass" if passed else "fail" if available else "unknown", reason)

        funds: CheckStatus = "unknown"
        funds_reason = "Fresh spending-currency availBal, valid order and fee coverage required."
        if (
            instrument_matches and balance_fresh and balance is not None
            and side_valid and amount_valid and price_valid and reserve is not None and reserve >= 0
        ):
            currency = instrument.quote_ccy if side == "buy" else instrument.base_ccy
            asset = next((item for item in balance.details if item.ccy == currency), None)
            available = _decimal(asset.avail_bal) if asset is not None else None
            if available is not None:
                required = (quantity * limit_price if side == "buy" else quantity) + reserve
                funds = "pass" if available >= 0 and available >= required else "fail"
                funds_reason = (
                    f"Exact {currency} availBal covers the order plus caller's fee reserve."
                    if funds == "pass" else
                    f"Exact {currency} availBal is negative or insufficient for order plus fees."
                )
            else:
                funds_reason = f"Exact {currency} availBal is missing, null, empty or invalid."
        add("available_funds", funds, funds_reason)

    for code, flag in (
        ("liability_free", liability_free),
        ("reconciliation", reconciliation_complete),
        ("sync_completeness", sync_complete),
    ):
        add(code, "pass" if flag is True else "fail" if flag is False else "unknown",
            f"Caller explicitly attests {code}." if flag is True else
            f"Caller explicitly rejects {code}." if flag is False else
            f"Explicit caller evidence for {code} is unavailable.")
    return SpotPreflightResult(tuple(checks))
