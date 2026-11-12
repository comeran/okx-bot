"""Detached exact observations selected together for read-only diagnostics."""

from dataclasses import dataclass

from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_balance_facts import AccountBalanceFact
from src.exchange.okx_instrument_facts import SpotInstrumentFact
from src.exchange.okx_trade_fee_facts import SpotTradeFeeFact


@dataclass(frozen=True)
class PreflightObservation[T]:
    fact: T
    payload_hash: str
    received_at: int


@dataclass(frozen=True)
class SpotPreflightEvidence:
    account_scope_id: str
    uid: str | None
    config: PreflightObservation[AccountConfigFact] | None
    balance: PreflightObservation[AccountBalanceFact] | None
    # A retained response can exist without a rule for the requested symbol.
    instrument: PreflightObservation[SpotInstrumentFact | None] | None
    trade_fee: PreflightObservation[SpotTradeFeeFact] | None = None
