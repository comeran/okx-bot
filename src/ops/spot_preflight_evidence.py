"""Read-only diagnostics from verified persisted observations, never authority."""

from dataclasses import asdict

from src.data.spot_preflight_evidence import SpotPreflightEvidence
from src.ops.spot_fee_diagnostic import evaluate_spot_fee_diagnostic
from src.ops.spot_liability_diagnostic import evaluate_spot_liability_diagnostic
from src.ops.spot_preflight import SpotPreflightResult, evaluate_spot_order_preflight
from src.ops.spot_sync_diagnostic import evaluate_spot_sync_diagnostic

PREFLIGHT_MAX_AGE_MS = 30_000


def build_stored_spot_preflight(
    evidence: SpotPreflightEvidence, *, symbol: str, side: str, amount: str,
    price: str, now_ms: int,
) -> dict[str, object]:
    """Use source receipt times and leave unproved operational gates unknown."""
    config, balance, instrument = evidence.config, evidence.balance, evidence.instrument
    trade_fee = evidence.trade_fee
    liability_diagnostic = evaluate_spot_liability_diagnostic(
        config=config.fact if config is not None else None,
        balance=balance.fact if balance is not None else None,
        now_ms=now_ms,
        config_received_at=config.received_at if config is not None else None,
        balance_received_at=balance.received_at if balance is not None else None,
        max_age_ms=PREFLIGHT_MAX_AGE_MS, expected_uid=evidence.uid,
        balance_uid=evidence.uid if balance is not None else None,
    )
    result = evaluate_spot_order_preflight(
        config=config.fact if config is not None else None,
        balance=balance.fact if balance is not None else None,
        instrument=instrument.fact if instrument is not None else None,
        symbol=symbol, side=side, amount=amount, price=price, now_ms=now_ms,
        config_received_at=config.received_at if config is not None else None,
        balance_received_at=balance.received_at if balance is not None else None,
        instrument_received_at=instrument.received_at if instrument is not None else None,
        max_age_ms=PREFLIGHT_MAX_AGE_MS,
        expected_uid=evidence.uid,
        balance_uid=evidence.uid if balance is not None else None,
        liability_free=liability_diagnostic.liability_free,
    )
    fee_diagnostic = evaluate_spot_fee_diagnostic(
        config=config.fact if config is not None else None,
        instrument=instrument.fact if instrument is not None else None,
        fee=trade_fee.fact if trade_fee is not None else None,
        symbol=symbol, side=side, amount=amount, price=price, now_ms=now_ms,
        config_received_at=config.received_at if config is not None else None,
        instrument_received_at=instrument.received_at if instrument is not None else None,
        fee_received_at=trade_fee.received_at if trade_fee is not None else None,
        max_age_ms=PREFLIGHT_MAX_AGE_MS, expected_uid=evidence.uid,
        fee_uid=evidence.uid if trade_fee is not None else None,
    )
    sync_diagnostic = evaluate_spot_sync_diagnostic(
        latest_attempt=evidence.sync_latest_attempt,
        last_committed=evidence.sync_last_committed,
        account_scope_id=evidence.account_scope_id, symbol=symbol, now_ms=now_ms,
        max_age_ms=PREFLIGHT_MAX_AGE_MS,
    )
    # Unrounded scenarios cannot supply the preflight's fee_reserve evidence.
    result = SpotPreflightResult(
        result.checks + fee_diagnostic.checks + liability_diagnostic.checks
        + sync_diagnostic.checks,
    )

    def provenance(observation):
        return None if observation is None else {
            "payload_hash": observation.payload_hash,
            "received_at": observation.received_at,
            "fact_present": observation.fact is not None,
        }

    return {
        "diagnostic_only": True, "evaluated_at": now_ms, "max_age_ms": PREFLIGHT_MAX_AGE_MS,
        "evidence": {
            "account_scope_id": evidence.account_scope_id, "uid": evidence.uid,
            "config": provenance(config), "balance": provenance(balance),
            "instrument": provenance(instrument),
            "trade_fee": provenance(trade_fee),
        },
        "fee_diagnostic": {
            field: getattr(fee_diagnostic, field) for field in (
                "group_id", "currency", "maker_rate", "taker_rate", "maker_signed_amount",
                "taker_signed_amount", "worst_commission_unrounded",
            )
        } | {
            "amounts_unrounded": True, "coverage": "unknown",
            "sign_convention": "positive_rebate_negative_commission",
        },
        "sync_diagnostic": {
            "coverage": "unknown", "latest_attempt": sync_diagnostic.latest_attempt,
            "last_committed": sync_diagnostic.last_committed,
        },
        "checks": [asdict(check) for check in result.checks],
        "liability_diagnostic": {"coverage": "unknown"} | {
            field: getattr(liability_diagnostic, field) for field in (
                "observed_currency_count", "reported_liability_currency_count",
                "negative_cash_currency_count", "uninterpretable_liability_field_count",
                "uninterpretable_cash_field_count",
            )
        },
        "can_submit": result.can_submit, "can_activate": result.can_activate,
    }
