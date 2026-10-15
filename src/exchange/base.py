from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from ccxt.base.decimal_to_precision import (
    DECIMAL_PLACES,
    SIGNIFICANT_DIGITS,
    TICK_SIZE,
)
from ccxt.base.errors import NotSupported

from src.core.types import (
    AccountSnapshot,
    AssetBalance,
    Bar,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionSnapshot,
    _safe_timestamp,
    _validate_amount_reconciliation,
    execution_type_family,
)
from src.exchange.errors import AcceptedUnknownOrderResultError, MalformedProviderData
from src.exchange.okx_client import create_okx_client
from src.order.router import OrderHandler

logger = logging.getLogger(__name__)


class RecentTradePage(list[ExchangeTradeSnapshot]):
    def __init__(
        self,
        trades: list[ExchangeTradeSnapshot],
        *,
        since: int | None,
        complete: bool,
        mode: str | None = None,
    ) -> None:
        super().__init__(trades)
        self.mode = mode or ("since" if since is not None else "recent")
        self.since = since
        self.complete = complete


_OPTION_NATIVE_RE = re.compile(
    r"^(?P<base>[A-Z0-9]+)-(?P<quote>[A-Z0-9]+)-(?P<expiry>[0-9]{6})-"
    r"(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<option_type>[CP])$"
)
_OPTION_UNIFIED_RE = re.compile(
    r"^(?P<base>[A-Z0-9]+)/(?P<quote>[A-Z0-9]+):(?P<settle>[A-Z0-9]+)-"
    r"(?P<expiry>[0-9]{6})-(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<option_type>[CP])$"
)
_MARKET_TYPE_ALIASES = {
    "futures": "future",
    "future": "future",
    "options": "option",
    "option": "option",
}


def _canonical_market_type(value: object) -> str:
    normalized = str(value or "auto").strip().lower()
    return _MARKET_TYPE_ALIASES.get(normalized, normalized)


_SUPPORTED_CANONICAL_RE = re.compile(
    r"(?:[A-Z0-9]+-[A-Z0-9]+(?:-SWAP|-[0-9]{6})?|"
    r"[A-Z0-9]+-[A-Z0-9]+-[0-9]{6}-[0-9]+(?:\.[0-9]+)?-[CP]|"
    r"[A-Z0-9]+/[A-Z0-9]+:[A-Z0-9]+|"
    r"[A-Z0-9]+/[A-Z0-9]+:[A-Z0-9]+-[0-9]{6}|"
    r"[A-Z0-9]+/[A-Z0-9]+:[A-Z0-9]+-[0-9]{6}-[0-9]+(?:\.[0-9]+)?-[CP])"
)


def _native_settle(base: str, quote: str) -> str:
    """Infer the settlement currency omitted by OKX native instrument IDs."""
    return base if quote == "USD" else quote


def _option_components(
    value: str,
) -> tuple[str, str, str, str, str, str] | None:
    match = _OPTION_NATIVE_RE.fullmatch(value) or _OPTION_UNIFIED_RE.fullmatch(value)
    if match is None:
        return None
    groups = match.groupdict()
    base = groups["base"]
    quote = groups["quote"]
    settle = groups.get("settle") or _native_settle(base, quote)
    return tuple(
        groups[field]
        for field in ("base", "quote")
    ) + (
        settle,
        groups["expiry"],
        groups["strike"],
        groups["option_type"],
    )


def _option_native_alias(value: str) -> str | None:
    components = _option_components(value)
    if components is None:
        return None
    base, quote, settle, expiry, strike, option_type = components
    if _OPTION_UNIFIED_RE.fullmatch(value) and settle != _native_settle(base, quote):
        return None
    return f"{base}-{quote}-{expiry}-{strike}-{option_type}"


def _option_unified_alias(value: str) -> str | None:
    components = _option_components(value)
    if components is None:
        return None
    base, quote, settle, expiry, strike, option_type = components
    return f"{base}/{quote}:{settle}-{expiry}-{strike}-{option_type}"


def _future_components(value: str) -> tuple[str, str, str, str] | None:
    if value.count("/") == 1 and value.count(":") == 1:
        base_quote, settle_expiry = value.split(":", 1)
        if base_quote.count("/") != 1 or settle_expiry.count("-") != 1:
            return None
        base, quote = base_quote.split("/", 1)
        settle, expiry = settle_expiry.split("-", 1)
        if not all((base, quote, settle, expiry)) or not expiry.isdigit():
            return None
        return base, quote, settle, expiry
    if "/" not in value and ":" not in value and value.count("-") == 2:
        base, quote, expiry = value.split("-")
        if not all((base, quote, expiry)) or expiry == "SWAP" or not expiry.isdigit():
            return None
        return base, quote, _native_settle(base, quote), expiry
    return None


def _dated_future_alias(value: str) -> str | None:
    components = _future_components(value)
    if components is None:
        return None
    base, quote, settle, expiry = components
    if value.count("/") == 1 and settle != _native_settle(base, quote):
        return None
    return f"{base}-{quote}-{expiry}"


def _hyphenated_future_alias(value: str) -> str | None:
    components = _future_components(value)
    if components is None or value.count("/") != 0:
        return None
    base, quote, settle, expiry = components
    return f"{base}/{quote}:{settle}-{expiry}"


def _symbol_variants(symbol: object, market_type: str) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    option_native = _option_native_alias(value)
    if option_native is not None:
        return {value, option_native, _option_unified_alias(value) or value}
    variants = {value}
    if market_type == "spot":
        if value.count("/") == 1:
            variants.add(value.replace("/", "-"))
        elif value.count("-") == 1:
            variants.add(value.replace("-", "/"))
    else:
        dated_alias = _dated_future_alias(value)
        if dated_alias is not None:
            variants.add(dated_alias)
        else:
            unified_alias = _hyphenated_future_alias(value)
            if unified_alias is not None:
                variants.add(unified_alias)
            elif value.count("/") == 1 and ":" in value:
                base_quote, settle = value.split(":", 1)
                base, quote = base_quote.split("/", 1)
                if settle == _native_settle(base, quote):
                    variants.add(f"{base}-{quote}-SWAP")
            elif value.endswith("-SWAP") and value.count("-") == 2:
                base, quote, _ = value.split("-")
                variants.add(f"{base}/{quote}:{_native_settle(base, quote)}")
    return variants


def is_supported_canonical_symbol(
    symbol: object, *, market_type: str = "auto"
) -> bool:
    value = str(symbol or "").strip().upper()
    normalized_type = _canonical_market_type(market_type)
    if not value:
        return False
    if normalized_type == "auto":
        return bool(_SUPPORTED_CANONICAL_RE.fullmatch(value))
    if normalized_type == "spot":
        return bool(re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", value))
    if normalized_type == "swap":
        return bool(
            re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", value)
            or re.fullmatch(r"[A-Z0-9]+/[A-Z0-9]+:[A-Z0-9]+", value)
        )
    if normalized_type in {"future", "futures"}:
        return bool(
            re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-[0-9]{6}", value)
            or re.fullmatch(r"[A-Z0-9]+/[A-Z0-9]+:[A-Z0-9]+-[0-9]{6}", value)
        )
    if normalized_type in {"option", "options"}:
        return bool(
            _OPTION_NATIVE_RE.fullmatch(value) or _OPTION_UNIFIED_RE.fullmatch(value)
        )
    return False


def canonical_symbol(
    symbol: object,
    *,
    market_type: str,
    markets: Mapping[object, object] | None = None,
) -> str:
    """Return one exact symbol identity without base/quote fuzzy matching."""
    value = str(symbol or "").strip().upper()
    market_type = _canonical_market_type(market_type)
    if not value:
        return ""
    option_native = _option_native_alias(value)
    if option_native is None and market_type == "auto":
        option_match = _OPTION_UNIFIED_RE.fullmatch(value)
        if option_match is not None:
            groups = option_match.groupdict()
            if groups["settle"] == groups["quote"]:
                option_native = (
                    f"{groups['base']}-{groups['quote']}-{groups['expiry']}-"
                    f"{groups['strike']}-{groups['option_type']}"
                )
    variants = _symbol_variants(value, market_type)
    if option_native is not None:
        value = option_native
    if markets:
        for market_key, raw_market in markets.items():
            if not isinstance(raw_market, Mapping):
                continue
            raw_market_type = _canonical_market_type(raw_market.get("type"))
            if (
                market_type != "auto"
                and raw_market_type
                and raw_market_type != _canonical_market_type(market_type)
            ):
                continue
            candidates = {
                market_key,
                raw_market.get("id"),
                raw_market.get("symbol"),
                raw_market.get("key"),
            }
            if variants & {
                candidate_variant
                for candidate in candidates
                if candidate
                for candidate_variant in _symbol_variants(candidate, market_type)
            }:
                return str(raw_market.get("id") or market_key).strip().upper()
    if market_type in {"spot", "auto"} and value.count("/") == 1 and ":" not in value:
        return value.replace("/", "-")
    if market_type != "spot":
        dated_alias = _dated_future_alias(value)
        if dated_alias is not None:
            return dated_alias
        if value.count("/") == 1 and ":" in value:
            base_quote, settle = value.split(":", 1)
            base, quote = base_quote.split("/", 1)
            if settle == _native_settle(base, quote):
                return f"{base}-{quote}-SWAP"
    return value


def safe_ohlcv_bar(row: object) -> Bar | None:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    raw_timestamp = row[0]
    timestamp = _safe_timestamp(raw_timestamp)
    if timestamp == 0 and raw_timestamp not in (0, "0", 0.0):
        return None
    if any(isinstance(value, bool) for value in row[1:6]):
        return None
    try:
        values = [float(value) for value in row[1:6]]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        return None
    open_price, high, low, close, volume = values
    if high < low or not low <= open_price <= high or not low <= close <= high:
        return None
    return Bar(timestamp, open_price, high, low, close, volume)


class ExchangeAdapter(OrderHandler, ABC):
    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: int | None = None,
    ) -> list[Bar]:
        pass

    @abstractmethod
    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[dict[str, float | str]]:
        pass

    @abstractmethod
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        pass

    @abstractmethod
    async def fetch_position_snapshots(
        self, symbols: list[str] | None = None
    ) -> list[PositionSnapshot]:
        pass

    @abstractmethod
    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        pass

    @abstractmethod
    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[ExchangeTradeSnapshot]:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass


class OKXBaseAdapter(ExchangeAdapter):
    _SPOT_OHLCV_TIMEFRAMES = {
        "1m": ("1m", 60_000),
        "5m": ("5m", 300_000),
        "15m": ("15m", 900_000),
        "1h": ("1H", 3_600_000),
        "4h": ("4H", 14_400_000),
        "1d": ("1Dutc", 86_400_000),
    }

    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        default_type: str,
        demo: bool = True,
    ) -> None:
        self._default_type = default_type
        self._exchange = create_okx_client(
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            default_type=default_type,
            demo=demo,
        )

    def _to_ccxt_symbol(self, symbol: str) -> str:
        if self._default_type == "spot":
            base_quote = symbol.split("-")
            if len(base_quote) == 2:
                return "/".join(base_quote)
            return symbol

        markets = getattr(self._exchange, "markets", None)
        if isinstance(markets, Mapping):
            requested = canonical_symbol(
                symbol,
                market_type=self._default_type,
                markets=markets,
            )
            for market_key, raw_market in markets.items():
                if not isinstance(raw_market, Mapping):
                    continue
                candidates = (
                    market_key,
                    raw_market.get("id"),
                    raw_market.get("symbol"),
                    raw_market.get("key"),
                )
                if any(
                    candidate
                    and (
                        canonical_symbol(candidate, market_type=self._default_type)
                        == requested
                        or _symbol_variants(symbol, self._default_type)
                        & _symbol_variants(candidate, self._default_type)
                    )
                    for candidate in candidates
                ):
                    return str(raw_market.get("symbol") or market_key)

        option_alias = _option_unified_alias(symbol.upper())
        if option_alias is not None:
            return option_alias
        dated_alias = _hyphenated_future_alias(symbol.upper())
        if dated_alias is not None:
            return dated_alias
        if symbol.upper().endswith("-SWAP") and symbol.count("-") == 2:
            base, quote, _ = symbol.upper().split("-")
            return f"{base}/{quote}:{_native_settle(base, quote)}"
        return symbol

    def _from_ccxt_symbol(self, symbol: object) -> str:
        value = str(symbol or "")
        if self._default_type != "spot" or "/" not in value:
            return value
        return value.replace("/", "-")

    def canonical_symbol(self, symbol: object) -> str:
        markets = getattr(self._exchange, "markets", None)
        return canonical_symbol(
            symbol,
            market_type=self._default_type,
            markets=markets if isinstance(markets, Mapping) else None,
        )

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        if value is None or value == "" or isinstance(value, bool):
            return default
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return normalized if math.isfinite(normalized) else default

    def _account_float(
        self, row: Mapping[str, object], field: str, *, nonnegative: bool = False
    ) -> float | None:
        if field not in row or row[field] is None:
            return None
        value = row[field]
        if isinstance(value, bool):
            raise MalformedProviderData(f"Malformed provider account data (invalid {field})")
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(
                f"Malformed provider account data (invalid {field})"
            ) from exc
        if not math.isfinite(normalized) or (nonnegative and normalized < 0.0):
            raise MalformedProviderData(f"Malformed provider account data (invalid {field})")
        return normalized

    def _safe_price(self, value: object, default: float = 0.0) -> float:
        normalized = self._safe_float(value, default)
        return normalized if normalized >= 0.0 else default

    def _optional_nonnegative_float(self, value: object) -> float | None:
        if value is None or value == "" or isinstance(value, bool):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return normalized if math.isfinite(normalized) and normalized >= 0.0 else None

    def _provider_string(self, row: dict[str, object], field: str, row_kind: str) -> str:
        if field not in row or row[field] is None:
            return ""
        value = row[field]
        if not isinstance(value, str):
            raise MalformedProviderData(
                f"Malformed provider {row_kind} row (invalid {field}): {row!r}"
            )
        return value.strip()

    def _optional_safe_price(self, value: object) -> float | None:
        return self._optional_nonnegative_float(value)

    def _explicit_nonnegative_float(
        self, row: dict[str, object], field: str, *, default: float | None = None
    ) -> float | None:
        if field not in row or row[field] is None:
            return default
        value = row[field]
        if isinstance(value, bool):
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            )
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            ) from exc
        if not math.isfinite(normalized) or normalized < 0.0:
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            )
        return normalized

    def _position_float(
        self,
        row: dict[str, object],
        info: dict[str, object],
        row_field: str,
        info_field: str | None = None,
        *,
        default: float | None = None,
        nonnegative: bool = False,
    ) -> float | None:
        value = row.get(row_field) if row_field in row else None
        if value is None and info_field is not None:
            value = info.get(info_field) if info_field in info else None
        if value is None:
            return default
        if isinstance(value, bool):
            raise MalformedProviderData(
                f"Malformed provider position row (invalid {row_field}): {row!r}"
            )
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(
                f"Malformed provider position row (invalid {row_field}): {row!r}"
            ) from exc
        if not math.isfinite(normalized) or (nonnegative and normalized < 0.0):
            raise MalformedProviderData(
                f"Malformed provider position row (invalid {row_field}): {row!r}"
            )
        return normalized

    def _position_market(self, symbol: str) -> dict[str, object] | None:
        markets = getattr(self._exchange, "markets", None)
        if not isinstance(markets, Mapping):
            return None
        requested = _symbol_variants(symbol, self._default_type)
        for market_key, raw_market in markets.items():
            if not isinstance(raw_market, Mapping):
                continue
            market_type = _canonical_market_type(raw_market.get("type"))
            if market_type and market_type != _canonical_market_type(self._default_type):
                continue
            candidates = (
                market_key,
                raw_market.get("id"),
                raw_market.get("symbol"),
                raw_market.get("key"),
            )
            if requested & {
                variant
                for candidate in candidates
                if candidate
                for variant in _symbol_variants(candidate, self._default_type)
            }:
                return dict(raw_market)
        return None

    @staticmethod
    def _valid_contract_size(value: object) -> float | None:
        if value is None or value == "" or isinstance(value, bool):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return normalized if math.isfinite(normalized) and normalized > 0.0 else None

    def _position_is_inverse(self, symbol: str) -> bool:
        market = self._position_market(symbol)
        if market is not None:
            if market.get("inverse") is True:
                return True
            settle = str(market.get("settle") or "").strip().upper()
            base = str(market.get("base") or "").strip().upper()
            if settle and base and settle == base:
                return True
        value = str(symbol or "").strip().upper()
        parts = value.split("-")
        return len(parts) > 1 and parts[1] == "USD"

    def _position_contract_size(
        self,
        symbol: str,
        row: dict[str, object],
        info: dict[str, object],
        *,
        is_inverse: bool = False,
    ) -> float:
        market = self._position_market(symbol)
        if market is not None:
            market_values: list[tuple[str, object]] = []
            if "contractSize" in market:
                market_values.append(("contractSize", market["contractSize"]))
            market_info = market.get("info")
            if isinstance(market_info, Mapping):
                for field in ("contractSize", "ctVal"):
                    if field in market_info:
                        market_values.append((field, market_info[field]))
            present_market_values = [
                (field, value) for field, value in market_values
                if value is not None and value != ""
            ]
            if present_market_values:
                normalized_values = [
                    self._valid_contract_size(value) for _, value in present_market_values
                ]
                if any(value is None for value in normalized_values):
                    raise MalformedProviderData(
                        f"Malformed provider position row (invalid contract size): {row!r}"
                    )
                if any(
                    not math.isclose(value, normalized_values[0], rel_tol=0.0, abs_tol=1e-12)
                    for value in normalized_values[1:]
                ):
                    raise MalformedProviderData(
                        f"Malformed provider position row (conflicting contract size): {row!r}"
                    )
                return normalized_values[0]

        candidates: list[object] = []
        candidates.extend(row.get(field) for field in ("contractSize", "contract_size"))
        candidates.extend(info.get(field) for field in ("contractSize", "ctVal"))
        explicit_size_seen = False
        for candidate in candidates:
            if candidate is None or candidate == "":
                continue
            explicit_size_seen = True
            normalized = self._valid_contract_size(candidate)
            if normalized is not None:
                return normalized
        if explicit_size_seen:
            raise MalformedProviderData(
                f"Malformed provider position row (invalid contract size): {row!r}"
            )
        if is_inverse:
            raise MalformedProviderData(
                f"Malformed provider position row (missing contract size): {row!r}"
            )
        # Legacy linear derivatives may omit contract size; retain the migration
        # compatibility fallback only when the instrument is not inverse.
        return 1.0

    def _position_leverage(
        self, row: dict[str, object], info: dict[str, object]
    ) -> int:
        value = self._position_float(row, info, "leverage", "lever", default=1.0)
        if value is None or value < 1.0 or value > 2**63 - 1 or not value.is_integer():
            raise MalformedProviderData(
                f"Malformed provider position row (invalid leverage): {row!r}"
            )
        return int(value)

    def _position_timestamp(self, row: dict[str, object], info: dict[str, object]) -> int:
        value = self._position_float(row, info, "timestamp", "uTime", default=0.0)
        if value is None or value < 0.0 or value > 2**63 - 1 or not value.is_integer():
            raise MalformedProviderData(
                f"Malformed provider position row (invalid timestamp): {row!r}"
            )
        return int(value)

    def _map_optional_order_amount(self, row: dict[str, object], field: str) -> float | None:
        if field not in row or row[field] is None:
            return None
        value = row[field]
        if isinstance(value, bool):
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            )
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            ) from exc
        if not math.isfinite(normalized) or normalized < 0.0:
            raise MalformedProviderData(
                f"Malformed provider open-order row (invalid {field}): {row!r}"
            )
        return normalized

    def _map_submit_fill_amount(
        self, response: dict[str, object], field: str, amount: float
    ) -> float | None:
        if field not in response or response[field] is None:
            return None
        value = response[field]
        if isinstance(value, bool):
            raise MalformedProviderData(f"invalid {field}")
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(f"invalid {field}") from exc
        if not math.isfinite(normalized) or not 0.0 <= normalized <= amount:
            raise MalformedProviderData(f"invalid {field}")
        return normalized

    def _safe_int(self, value: object, default: int = 0) -> int:
        normalized = _safe_timestamp(value)
        return normalized if normalized != 0 or value in (0, "0", 0.0) else default

    def _balance_value(self, balance: dict[str, object], currency: str, field_name: str) -> object:
        row = balance.get(currency)
        if isinstance(row, dict) and row.get(field_name) is not None:
            return row.get(field_name)
        field = balance.get(field_name)
        if isinstance(field, dict):
            return field.get(currency)
        return None

    def _balance_currency(self, balance: dict[str, object]) -> str:
        total = balance.get("total")
        if isinstance(total, dict) and total:
            return str(next(iter(total)))
        free = balance.get("free")
        if isinstance(free, dict) and free:
            return str(next(iter(free)))
        return next(
            (key for key, value in balance.items() if isinstance(value, dict) and key != "info"), ""
        )

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        balance = await self._exchange.fetch_balance()
        if not isinstance(balance, dict):
            raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
        info = balance.get("info")
        if not isinstance(info, dict):
            raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
        if "data" in info:
            data = info["data"]
            if not isinstance(data, (list, tuple)):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
        elif any(key in balance for key in ("total", "free")):
            data = []
        else:
            raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
        if data:
            if any(not isinstance(row, dict) or not row for row in data):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
            if any(
                not ({"totalEq", "availEq", "upl", "details"} & set(row))
                for row in data
            ):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
            if any(
                "details" in row and not isinstance(row["details"], (list, tuple))
                for row in data
            ):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
            if any(
                set(row) == {"details"} and not row["details"]
                for row in data
            ):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
            if any(
                isinstance(row.get("details"), (list, tuple))
                and any(not isinstance(detail, dict) or not detail or not detail.get("ccy")
                        for detail in row["details"])
                for row in data
            ):
                raise MalformedProviderData(f"Malformed provider balance response: {balance!r}")
        account_row = next((row for row in data if isinstance(row, dict)), {})
        details = [
            detail
            for account in data
            if isinstance(account, dict)
            for detail in (
                account.get("details") if isinstance(account.get("details"), (list, tuple)) else []
            )
            if isinstance(detail, dict)
        ]
        for account in data:
            for field in ("totalEq", "availEq"):
                self._account_float(account, field, nonnegative=True)
            self._account_float(account, "upl")
        for detail in details:
            for field in ("cashBal", "eq", "eqUtd", "eqUsd", "availBal"):
                self._account_float(detail, field, nonnegative=True)
            for field in ("upl", "realizedPnl"):
                self._account_float(detail, field)
        for currency in (self._balance_currency(balance), "USDT"):
            if currency:
                for field in ("total", "free"):
                    raw_value = self._balance_value(balance, currency, field)
                    if raw_value is not None:
                        self._account_float({field: raw_value}, field, nonnegative=True)
        assets = [
            AssetBalance(
                ccy=str(detail.get("ccy") or ""),
                cash_bal=self._safe_float(detail.get("cashBal")),
                eq=self._safe_float(detail.get("eq")),
                eq_utd=self._safe_float(detail.get("eqUtd"), self._safe_float(detail.get("eqUsd"))),
                avail_bal=self._safe_float(detail.get("availBal")),
                upl=self._safe_float(detail.get("upl")),
            )
            for detail in details
            if detail.get("ccy")
        ]

        currency = "USDT"
        row = next((detail for detail in details if detail.get("ccy") == currency), None)
        fallback_total = self._safe_float(self._balance_value(balance, currency, "total"))
        fallback_free = self._safe_float(self._balance_value(balance, currency, "free"))
        assets_equity = sum(asset.eq_utd for asset in assets)
        updated_at = self._safe_int(
            row.get("uTime") if row is not None else None,
            self._safe_int(
                account_row.get("uTime"),
                self._safe_int(info.get("uTime"), self._safe_int(info.get("ts"))),
            ),
        )
        return AccountSnapshot(
            currency=currency,
            equity=self._safe_float(
                account_row.get("totalEq"),
                assets_equity if assets_equity else fallback_total,
            ),
            cash_balance=self._safe_float(
                row.get("cashBal") if row is not None else None,
                fallback_free,
            ),
            available_balance=self._safe_float(
                row.get("availBal") if row is not None else None,
                self._safe_float(account_row.get("availEq"), fallback_free),
            ),
            unrealized_pnl=self._safe_float(
                account_row.get("upl"),
                sum(asset.upl for asset in assets),
            ),
            realized_pnl=sum(self._safe_float(detail.get("realizedPnl")) for detail in details),
            updated_at=updated_at,
            assets=assets,
        )

    async def fetch_position_snapshots(
        self, symbols: list[str] | None = None
    ) -> list[PositionSnapshot]:
        markets = getattr(self._exchange, "markets", None)
        if not markets and hasattr(self._exchange, "load_markets"):
            loaded_markets = await self._exchange.load_markets()
            if isinstance(loaded_markets, Mapping):
                self._exchange.markets = loaded_markets
        ccxt_symbols = (
            [self._to_ccxt_symbol(symbol) for symbol in symbols]
            if symbols is not None
            else None
        )
        rows = await self._exchange.fetch_positions(ccxt_symbols)
        if not isinstance(rows, (list, tuple)):
            raise MalformedProviderData(f"Malformed provider position response: {rows!r}")
        snapshots = []
        for row in rows:
            snapshots.append(self._parse_position_snapshot(row))
        return snapshots

    def _parse_position_snapshot(self, row: dict[str, object]) -> PositionSnapshot:
        if not isinstance(row, dict) or not row:
            raise MalformedProviderData(f"Malformed provider position row: {row!r}")
        if not isinstance(row.get("info", {}), dict):
            raise MalformedProviderData(
                f"Malformed provider position row (invalid info): {row!r}"
            )
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        symbol = str(info.get("instId") or row.get("symbol") or "").strip()
        if not symbol:
            raise MalformedProviderData(
                f"Malformed provider position row (missing symbol): {row!r}"
            )
        raw_amount_field = next(
            (
                field
                for field in ("contracts", "amount", "positionAmt")
                if field in row and row[field] not in (None, "")
            ),
            "info.pos"
            if "pos" in info and info["pos"] not in (None, "")
            else None,
        )
        raw_amount = (
            row[raw_amount_field]
            if raw_amount_field in row
            else info.get("pos")
            if raw_amount_field == "info.pos"
            else None
        )
        if raw_amount is None or raw_amount == "":
            raise MalformedProviderData(
                f"Malformed provider position row (missing amount): {row!r}"
            )
        if isinstance(raw_amount, bool):
            raise MalformedProviderData(
                f"Malformed provider position row (invalid amount): {row!r}"
            )
        try:
            signed_amount = float(raw_amount)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MalformedProviderData(
                f"Malformed provider position row (invalid amount): {row!r}"
            ) from exc
        if not math.isfinite(signed_amount):
            raise MalformedProviderData(
                f"Malformed provider position row (invalid amount): {row!r}"
            )

        signed_evidence: list[tuple[str, float]] = []
        raw_signed_fields = []
        if "positionAmt" in row:
            raw_signed_fields.append(("position amount", row["positionAmt"]))
        if "pos" in info:
            raw_signed_fields.append(("info position", info["pos"]))
        for field, value in raw_signed_fields:
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                raise MalformedProviderData(
                    f"Malformed provider position row (invalid {field}): {row!r}"
                )
            try:
                normalized = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise MalformedProviderData(
                    f"Malformed provider position row (invalid {field}): {row!r}"
                ) from exc
            if not math.isfinite(normalized):
                raise MalformedProviderData(
                    f"Malformed provider position row (invalid {field}): {row!r}"
                )
            signed_evidence.append((field, normalized))

        if self._default_type != "spot":
            signed_by_field = dict(signed_evidence)
            position_amt = signed_by_field.get("position amount")
            info_pos = signed_by_field.get("info position")
            if (
                position_amt is not None
                and info_pos is not None
                and (
                    abs(position_amt) != abs(info_pos)
                    or (
                        position_amt != 0.0
                        and info_pos != 0.0
                        and position_amt * info_pos < 0.0
                    )
                )
            ):
                raise MalformedProviderData(
                    f"Malformed provider position row (conflicting position amount): {row!r}"
                )
            if raw_amount_field in {"contracts", "amount", "positionAmt", "info.pos"}:
                for _, normalized in signed_evidence:
                    if abs(signed_amount) != abs(normalized):
                        raise MalformedProviderData(
                            "Malformed provider position row "
                            f"(conflicting position amount): {row!r}"
                        )

        side_amount = signed_amount
        if signed_evidence:
            # Prefer the normalized exchange field, then the raw OKX position.
            side_amount = signed_evidence[0][1]
        if side_amount == 0.0 and signed_evidence:
            side_amount = signed_evidence[0][1]
        side = self._position_side(row, info, side_amount)
        if side is None:
            raise MalformedProviderData(
                f"Malformed provider position row (invalid side): {row!r}"
            )
        amount = abs(signed_amount)
        contracts = None
        quantity = amount
        quantity_unit = "base"
        notional_value = None
        is_inverse = False
        contract_size = None
        if self._default_type != "spot" and raw_amount_field in {
            "contracts", "amount", "positionAmt", "info.pos"
        }:
            contracts = amount
            is_inverse = self._position_is_inverse(symbol)
            contract_size = self._position_contract_size(symbol, row, info, is_inverse=is_inverse)
            amount *= contract_size
            if not math.isfinite(amount) or amount < 0.0:
                raise MalformedProviderData(
                    f"Malformed provider position row (invalid position amount): {row!r}"
                )
            if is_inverse:
                quantity = contracts
                quantity_unit = "contracts"
                notional_value = contracts * contract_size
            else:
                quantity = amount
                mark_or_entry = self._position_float(
                    row, info, "markPrice", "markPx", nonnegative=True
                ) or self._position_float(
                    row, info, "entryPrice", "avgPx", default=0.0, nonnegative=True
                ) or 0.0
                notional_value = amount * mark_or_entry
        return PositionSnapshot(
            symbol=symbol,
            side=side,
            amount=amount,
            entry_price=self._position_float(
                row, info, "entryPrice", "avgPx", default=0.0, nonnegative=True
            ) or 0.0,
            mark_price=self._position_float(
                row, info, "markPrice", "markPx", nonnegative=True
            ),
            unrealized_pnl=self._position_float(
                row, info, "unrealizedPnl", "upl", default=0.0
            ) or 0.0,
            realized_pnl=self._position_float(
                row, info, "realizedPnl", "realizedPnl", default=0.0
            ) or 0.0,
            leverage=self._position_leverage(row, info),
            updated_at=self._position_timestamp(row, info),
            contracts=contracts,
            quantity=quantity,
            quantity_unit=quantity_unit,
            notional_value=notional_value,
            is_inverse=is_inverse,
            contract_size=contract_size,
        )

    def _position_side(
        self, row: dict[str, object], info: dict[str, object], amount: float
    ) -> PositionSide | None:
        explicit_values = [
            ("side", row["side"]) for _ in [0] if "side" in row
        ] + [
            ("posSide", row["posSide"]) for _ in [0] if "posSide" in row
        ] + [
            ("info.side", info["side"]) for _ in [0] if "side" in info
        ] + [
            ("info.posSide", info["posSide"]) for _ in [0] if "posSide" in info
        ]
        directions: list[PositionSide] = []
        for field, value in explicit_values:
            if value is None:
                continue
            normalized = str(value).strip().lower()
            if normalized in {"long", "buy"}:
                directions.append(PositionSide.LONG)
            elif normalized in {"short", "sell"}:
                directions.append(PositionSide.SHORT)
            elif normalized != "net":
                raise MalformedProviderData(
                    f"Malformed provider position row (invalid {field}): {row!r}"
                )

        if directions and any(direction != directions[0] for direction in directions[1:]):
            raise MalformedProviderData(
                "Malformed provider position row "
                f"(conflicting explicit position direction): {row!r}"
            )
        if directions and amount != 0.0:
            signed_direction = PositionSide.SHORT if amount < 0.0 else PositionSide.LONG
            if directions[0] != signed_direction:
                raise MalformedProviderData(
                    f"Malformed provider position row (conflicting position direction): {row!r}"
                )
            return directions[0]
        if directions:
            return directions[0]
        return PositionSide.SHORT if amount < 0 else PositionSide.LONG

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: int | None = None,
    ) -> list[Bar]:
        if self._default_type == "spot":
            okx_bar, duration_ms = self._SPOT_OHLCV_TIMEFRAMES[timeframe]
            params = {"instId": symbol, "bar": okx_bar, "limit": limit}
            if since is not None:
                params["before"] = max(since - 1, 0)
                params["after"] = since + duration_ms * limit
                response = await self._exchange.public_get_market_history_candles(params)
            else:
                response = await self._exchange.public_get_market_candles(params)
            if not isinstance(response, Mapping) or "data" not in response:
                raise MalformedProviderData(
                    f"Malformed provider OHLCV response envelope: {response!r}"
                )
            rows = response["data"]
            if not isinstance(rows, list):
                raise MalformedProviderData(
                    f"Malformed provider OHLCV data rows: {rows!r}"
                )
        else:
            rows = await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

        if not isinstance(rows, (list, tuple)):
            raise MalformedProviderData(f"Malformed provider OHLCV data rows: {rows!r}")
        valid_bars: list[Bar] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise MalformedProviderData(f"Malformed provider OHLCV row: {row!r}")
            bar = safe_ohlcv_bar(row)
            if bar is None:
                raise MalformedProviderData(f"Malformed provider OHLCV row: {row!r}")
            valid_bars.append(bar)
        return sorted(valid_bars, key=lambda bar: bar.timestamp)

    def _ticker_values(
        self,
        row: dict[str, object],
        *,
        bid_field: str,
        ask_field: str,
        volume_field: str,
    ) -> tuple[float, float, float, float] | None:
        values = [
            self._optional_nonnegative_float(row.get(field))
            for field in ("last", bid_field, ask_field)
        ]
        if any(value is None for value in values):
            return None
        volume = self._optional_nonnegative_float(row.get(volume_field))
        if volume is None:
            return None
        return values[0], values[1], values[2], volume

    def _ticker_symbol_aliases(self, symbol: str) -> set[str]:
        value = str(symbol or "").strip()
        aliases = {value} if value else set()
        option_native = _option_native_alias(value.upper())
        if option_native is not None:
            aliases.update({option_native, _option_unified_alias(option_native) or option_native})
            return aliases
        if value.count("/") == 1 and ":" not in value:
            base, quote = value.split("/")
            aliases.add(f"{base}-{quote}")
        elif value.count("-") == 1 and "/" not in value:
            base, quote = value.split("-")
            aliases.add(f"{base}/{quote}")
        elif value.endswith("-SWAP") and value.count("-") == 2:
            base, quote, _ = value.split("-")
            aliases.add(f"{base}/{quote}:{_native_settle(base, quote)}")
        elif value.count("/") == 1 and ":" in value:
            base_quote, settle = value.split(":", 1)
            base, quote = base_quote.split("/")
            if settle == _native_settle(base, quote):
                aliases.add(f"{base}-{quote}-SWAP")
        return aliases

    def _ticker_market_aliases(self, market_key: object, market: dict[str, object]) -> set[str]:
        aliases: set[str] = set()
        for value in (market_key, market.get("id"), market.get("symbol"), market.get("key")):
            if value:
                aliases.update(self._ticker_symbol_aliases(str(value)))
        return aliases

    def _ticker_market_for_symbol(
        self,
        symbol: str,
        markets: dict[object, object],
    ) -> tuple[dict[str, object], object] | None:
        requested_aliases = self._ticker_symbol_aliases(symbol)
        for market_key, raw_market in markets.items():
            if not isinstance(raw_market, dict):
                continue
            if requested_aliases & self._ticker_market_aliases(market_key, raw_market):
                return market_key, raw_market
        return None

    def _ticker_row_aliases(self, row_key: object, row: dict[str, object]) -> set[str]:
        aliases: set[str] = set()
        for value in (row_key, row.get("instId"), row.get("key"), row.get("symbol"), row.get("id")):
            if value:
                aliases.update(self._ticker_symbol_aliases(str(value)))
        return aliases

    def _ticker_row_for_symbol(
        self,
        symbol: str,
        rows: object,
        markets: dict[object, object],
    ) -> dict[str, object] | None:
        market_match = self._ticker_market_for_symbol(symbol, markets)
        aliases = (
            self._ticker_market_aliases(*market_match)
            if market_match is not None
            else self._ticker_symbol_aliases(symbol)
        )
        if not isinstance(rows, (dict, list, tuple)):
            logger.warning("Skipping malformed ticker rows: %r", rows)
            return None
        row_items = rows.items() if isinstance(rows, dict) else enumerate(rows)
        for row_key, raw_row in row_items:
            if isinstance(raw_row, dict) and aliases & self._ticker_row_aliases(row_key, raw_row):
                return raw_row
        return None

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[dict[str, float | str]]:
        if symbols == []:
            return []

        raw_markets = getattr(self._exchange, "markets", None)
        markets = raw_markets if isinstance(raw_markets, dict) else {}
        if not markets and hasattr(self._exchange, "load_markets"):
            loaded_markets = await self._exchange.load_markets()
            markets = loaded_markets if isinstance(loaded_markets, dict) else {}

        if self._default_type == "spot":
            response = await self._exchange.public_get_market_tickers({"instType": "SPOT"})
            rows = response.get("data", []) if isinstance(response, dict) else []
            if not isinstance(response, dict):
                logger.warning("Skipping malformed ticker response envelope: %r", response)
            bid_field, ask_field, volume_field = "bidPx", "askPx", "vol24h"
        else:
            ccxt_symbols = None if symbols is None else [
                self._to_ccxt_symbol(symbol) for symbol in symbols
            ]
            rows = (
                await self._exchange.fetch_tickers()
                if ccxt_symbols is None
                else await self._exchange.fetch_tickers(ccxt_symbols)
            )
            bid_field, ask_field, volume_field = "bid", "ask", "baseVolume"

        requested_symbols = symbols
        if requested_symbols is None:
            if isinstance(rows, dict):
                row_items = rows.items()
            elif isinstance(rows, (list, tuple)):
                row_items = enumerate(rows)
            else:
                row_items = ()
            requested_symbols = []
            for row_key, raw_row in row_items:
                if not isinstance(raw_row, dict):
                    continue
                source_symbol = next(
                    (
                        value
                        for value in (
                            raw_row.get("instId"),
                            raw_row.get("symbol"),
                            raw_row.get("id"),
                            raw_row.get("key"),
                            row_key,
                        )
                        if value
                    ),
                    None,
                )
                if source_symbol is not None:
                    requested_symbols.append(self.canonical_symbol(source_symbol))

        tickers = []
        for symbol in requested_symbols:
            row = self._ticker_row_for_symbol(symbol, rows, markets)
            if row is None:
                continue
            values = self._ticker_values(
                row, bid_field=bid_field, ask_field=ask_field, volume_field=volume_field
            )
            if values is None:
                continue
            last, bid, ask, volume = values
            response_symbol = (
                symbol if self._default_type == "spot" else self.canonical_symbol(symbol)
            )
            tickers.append(
                {
                    "symbol": response_symbol,
                    "last": last,
                    "bidPx": bid,
                    "askPx": ask,
                    "vol24h": volume,
                }
            )
        return tickers

    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        if symbols == []:
            return []
        orders: list[ExchangeOrderSnapshot] = []
        for symbol in symbols if symbols is not None else [None]:
            ccxt_symbol = self._to_ccxt_symbol(symbol) if symbol is not None else None
            for provider_name, params in (
                ("ordinary", None),
                ("trigger", {"trigger": True}),
            ):
                try:
                    if ccxt_symbol is None:
                        rows = (
                            await self._exchange.fetch_open_orders()
                            if params is None
                            else await self._exchange.fetch_open_orders(params=params)
                        )
                    else:
                        rows = (
                            await self._exchange.fetch_open_orders(ccxt_symbol)
                            if params is None
                            else await self._exchange.fetch_open_orders(
                                ccxt_symbol, params=params
                            )
                        )
                except NotSupported:
                    if provider_name == "trigger":
                        logger.warning(
                            "Trigger open-order endpoint is not supported for %s; "
                            "open-order completeness is unavailable",
                            self._default_type,
                        )
                    raise
                if not isinstance(rows, (list, tuple)):
                    raise MalformedProviderData(
                        "Malformed provider open-order response "
                        f"(provider={provider_name}): {rows!r}"
                    )
                for row in rows:
                    try:
                        snapshot = self._map_order(row)
                    except MalformedProviderData as exc:
                        raise MalformedProviderData(
                            f"Malformed provider open-order row (provider={provider_name}): {row!r}"
                        ) from exc
                    if snapshot is None:
                        raise MalformedProviderData(
                            f"Malformed provider open-order row (provider={provider_name}): {row!r}"
                        )
                    orders.append(snapshot)

        deduplicated: list[ExchangeOrderSnapshot] = []
        identity_indexes: dict[tuple[str, str], int] = {}
        for snapshot in orders:
            identities = [
                ("exchange", snapshot.exchange_order_id),
                ("client", snapshot.client_order_id),
            ]
            matching_indexes = {
                identity_indexes[identity]
                for identity in identities
                if identity[1] and identity in identity_indexes
            }
            if not matching_indexes:
                index = len(deduplicated)
                deduplicated.append(snapshot)
                for identity in identities:
                    if identity[1]:
                        identity_indexes[identity] = index
                continue

            index = min(matching_indexes)
            prior = deduplicated[index]
            if prior != snapshot:
                logger.warning(
                    "Conflicting duplicate open-order facts for external identity: "
                    "exchange_order_id=%s client_order_id=%s",
                    snapshot.exchange_order_id,
                    snapshot.client_order_id,
                )
                raise MalformedProviderData(
                    "Malformed provider open-order response: conflicting duplicate "
                    f"facts for {snapshot.exchange_order_id or snapshot.client_order_id}"
                )
            for identity in identities:
                if identity[1]:
                    identity_indexes[identity] = index
        return deduplicated

    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> RecentTradePage:
        if symbols == []:
            return RecentTradePage([], since=since, complete=True)
        trades: list[ExchangeTradeSnapshot] = []
        complete = True
        for symbol in symbols if symbols is not None else [None]:
            ccxt_symbol = self._to_ccxt_symbol(symbol) if symbol is not None else None
            rows = await self._exchange.fetch_my_trades(
                ccxt_symbol,
                since=since,
                limit=limit,
            )
            if not isinstance(rows, (list, tuple)):
                raise MalformedProviderData(f"Malformed provider recent-trade response: {rows!r}")
            complete = complete and since is not None and len(rows) < limit
            for row in rows:
                snapshot = self._map_trade(row)
                if snapshot is None:
                    raise MalformedProviderData(
                        "Malformed provider recent-trade row "
                        f"(missing external identity or invalid fields): {row!r}"
                    )
                trades.append(snapshot)
        return RecentTradePage(trades, since=since, complete=complete)

    async def submit(self, order: Order) -> Order:
        order_type, params = self._okx_order_type_and_params(order)
        await self._validate_order_against_market(order)
        response = await self._exchange.create_order(
            self._to_ccxt_symbol(order.symbol),
            order_type,
            order.side.value,
            order.amount,
            order.price,
            params,
        )
        if not isinstance(response, dict) or not response:
            raise AcceptedUnknownOrderResultError(
                f"Order submission result is unknown; reconciliation required: {response!r}"
            )
        order.exchange_order_id = str(response.get("id") or "")
        response_client_order_id = str(
            response.get("clientOrderId") or response.get("clientOid") or ""
        ).strip()
        if response_client_order_id:
            order.client_order_id = response_client_order_id
        if not order.exchange_order_id and not order.client_order_id:
            raise AcceptedUnknownOrderResultError(
                "Exchange accepted an unidentifiable order response; reconciliation required"
            )
        order.updated_at = self._safe_int(
            response.get("lastTradeTimestamp"),
            self._safe_int(response.get("updated"), self._safe_int(response.get("timestamp"))),
        )
        try:
            response_filled = self._map_submit_fill_amount(response, "filled", order.amount)
            response_remaining = self._map_submit_fill_amount(
                response, "remaining", order.amount
            )
            if response_filled is not None and response_remaining is not None:
                try:
                    _validate_amount_reconciliation(
                        order.amount,
                        response_filled,
                        response_remaining,
                    )
                except ValueError as exc:
                    raise MalformedProviderData(
                        "inconsistent filled and remaining"
                    ) from exc
        except MalformedProviderData as exc:
            malformed = AcceptedUnknownOrderResultError(
                f"Order accepted with malformed fill field; reconciliation required: {response!r}"
            )
            malformed.id = order.exchange_order_id
            malformed.clientOrderId = order.client_order_id
            raise malformed from exc
        response_status = self._map_status(response.get("status"))
        if response_remaining is not None and response_remaining <= order.amount:
            order.remaining_amount = response_remaining
            order.filled_amount = order.amount - response_remaining
        elif response_filled is not None:
            order.filled_amount = min(response_filled, order.amount)
            order.remaining_amount = order.amount - order.filled_amount
        elif response_status is OrderStatus.FILLED:
            order.filled_amount = order.amount
            order.remaining_amount = 0.0
        else:
            order.filled_amount = 0.0
            order.remaining_amount = order.amount

        if response_status in {OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            order.status = response_status
        elif order.filled_amount >= order.amount and order.amount > 0.0:
            order.status = OrderStatus.FILLED
        elif order.filled_amount > 0.0:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.PENDING

        fill_price = self._optional_safe_price(response.get("average"))
        if fill_price is not None:
            order.fill_price = fill_price
        if order.status is OrderStatus.FILLED:
            timestamp = response.get("timestamp")
            if timestamp is not None:
                order.fill_time = self._safe_int(timestamp)
        return order

    def _okx_order_type_and_params(self, order: Order) -> tuple[str, dict[str, object]]:
        params = dict(order.params)
        client_order_id = str(order.client_order_id or "").strip()
        if client_order_id:
            params["clientOrderId"] = client_order_id
        if self._default_type != "spot":
            params.setdefault("tdMode", "cross")
        order_type = order.type.value
        if order.type == OrderType.STOP or order.type.value == "stop":
            if order.trigger_price is None:
                raise ValueError("Stop orders require trigger_price")
            order_type = "market" if order.price is None else "limit"
            params["triggerPrice"] = order.trigger_price
        if order.stop_loss is not None:
            params["stopLoss"] = {"triggerPrice": order.stop_loss}
        if order.take_profit is not None:
            params["takeProfit"] = {"triggerPrice": order.take_profit}
        return order_type, params

    async def _validate_order_against_market(self, order: Order) -> None:
        markets = getattr(self._exchange, "markets", None)
        if not markets and hasattr(self._exchange, "load_markets"):
            markets = await self._exchange.load_markets()
        markets = markets or {}
        market = markets.get(order.symbol) or markets.get(self._to_ccxt_symbol(order.symbol))
        if not market:
            market_lookup = getattr(self._exchange, "market", None)
            if callable(market_lookup):
                try:
                    candidate = market_lookup(order.symbol)
                except Exception:
                    candidate = None
                if isinstance(candidate, dict):
                    market = candidate
        if not market:
            raise ValueError(f"Exchange market metadata unavailable for {order.symbol}")

        limits = market.get("limits") or {}
        amount_min = self._optional_nonnegative_float((limits.get("amount") or {}).get("min"))
        if amount_min is not None and order.amount < amount_min:
            raise ValueError("amount below exchange minimum")

        if order.price is not None:
            price_min = self._optional_nonnegative_float((limits.get("price") or {}).get("min"))
            if price_min is not None and order.price < price_min:
                raise ValueError("price below exchange minimum")
            cost_min = self._optional_nonnegative_float((limits.get("cost") or {}).get("min"))
            if cost_min is not None and order.amount * order.price < cost_min:
                raise ValueError("cost below exchange minimum")

        precision = market.get("precision") or {}
        precision_mode = market.get("precisionMode")
        if precision_mode is None:
            precision_mode = getattr(self._exchange, "precisionMode", DECIMAL_PLACES)
        self._validate_precision(
            order.amount,
            precision.get("amount"),
            "amount",
            precision_mode,
        )
        if order.price is not None:
            self._validate_precision(
                order.price,
                precision.get("price"),
                "price",
                precision_mode,
            )

    def _validate_precision(
        self,
        value: float,
        precision: object,
        field_name: str,
        precision_mode: object,
    ) -> None:
        if precision is None:
            return
        if precision_mode == TICK_SIZE:
            try:
                decimal_value = Decimal(str(value))
                tick_size = Decimal(str(precision))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid {field_name}") from exc
            if not decimal_value.is_finite() or not tick_size.is_finite():
                raise ValueError(f"invalid {field_name}")
            if tick_size <= 0:
                return
            if decimal_value % tick_size != 0:
                raise ValueError(f"{field_name} precision exceeds exchange precision")
            return
        if not isinstance(precision, int) or precision < 0:
            return
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid {field_name}") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"invalid {field_name}")
        if precision_mode == SIGNIFICANT_DIGITS:
            if decimal_value != 0 and len(decimal_value.normalize().as_tuple().digits) > precision:
                raise ValueError(f"{field_name} precision exceeds exchange precision")
            return
        if decimal_value.as_tuple().exponent < -precision:
            raise ValueError(f"{field_name} precision exceeds exchange precision")

    async def cancel(self, order_id: str, symbol: str | None = None) -> object:
        if symbol is None:
            raise ValueError("OKX cancel requires symbol")
        return await self._exchange.cancel_order(order_id, self._to_ccxt_symbol(symbol))

    async def close(self) -> None:
        await self._exchange.close()

    def _order_side(self, value: object, row_kind: str) -> OrderSide | None:
        normalized = str(value or "").strip().lower()
        side = {
            "buy": OrderSide.BUY,
            "long": OrderSide.BUY,
            "sell": OrderSide.SELL,
            "short": OrderSide.SELL,
        }.get(normalized)
        if side is None:
            logger.warning("Unknown exchange %s side %r; skipping row", row_kind, value)
        return side

    def _map_order(self, row: dict[str, object]) -> ExchangeOrderSnapshot:
        def malformed(reason: str) -> None:
            raise MalformedProviderData(f"Malformed provider open-order row ({reason}): {row!r}")

        if not isinstance(row, dict):
            malformed("row is not an object")
        symbol = self._from_ccxt_symbol(
            self._provider_string(row, "symbol", "open-order")
        ).strip()
        if not symbol:
            malformed("missing symbol")
        side = self._order_side(row.get("side"), "order")
        if side is None:
            malformed("invalid side")
        timestamp = self._safe_int(row.get("timestamp"))
        amount = self._optional_nonnegative_float(row.get("amount"))
        if amount is None or amount <= 0.0:
            malformed("invalid amount")
        filled_amount = self._map_optional_order_amount(row, "filled")
        remaining_amount = self._map_optional_order_amount(row, "remaining")
        if filled_amount is not None and filled_amount > amount:
            malformed("filled exceeds amount")
        if remaining_amount is not None and remaining_amount > amount:
            malformed("remaining exceeds amount")
        if filled_amount is not None and remaining_amount is not None:
            try:
                _validate_amount_reconciliation(
                    amount,
                    filled_amount,
                    remaining_amount,
                )
            except ValueError as exc:
                raise MalformedProviderData(
                    "Malformed provider open-order row "
                    f"(inconsistent filled and remaining): {row!r}"
                ) from exc
        raw_type_value = row.get("type")
        if not isinstance(raw_type_value, str) or not raw_type_value.strip():
            malformed("invalid type")
        raw_type = raw_type_value.strip().lower()
        order_type = {
            "stop_limit": OrderType.LIMIT,
            "stop-limit": OrderType.LIMIT,
            "stop-market": OrderType.MARKET,
            "stop_market": OrderType.MARKET,
            "trigger": OrderType.MARKET,
            "trigger_limit": OrderType.LIMIT,
            "trigger-limit": OrderType.LIMIT,
            "trigger_market": OrderType.MARKET,
            "trigger-market": OrderType.MARKET,
        }.get(raw_type)
        if order_type is None:
            try:
                order_type = OrderType(raw_type)
            except ValueError:
                malformed("invalid type")
        price = self._explicit_nonnegative_float(row, "price")
        if raw_type in {"limit", "stop_limit", "trigger_limit"} and (
            price is None or price <= 0.0
        ):
            malformed("limit order missing or invalid price")
        exchange_order_id = self._provider_string(row, "id", "open-order")
        client_order_id = self._provider_string(row, "clientOrderId", "open-order")
        client_oid = self._provider_string(row, "clientOid", "open-order")
        if not client_order_id:
            client_order_id = client_oid
        if not exchange_order_id and not client_order_id:
            malformed("missing external identity")
        return ExchangeOrderSnapshot(
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price or 0.0,
            status=self._map_status(
                row.get("status"),
                filled_amount=filled_amount or 0.0,
                amount=amount,
                remaining_amount=remaining_amount,
            ),
            fill_price=(
                self._explicit_nonnegative_float(row, "average", default=None)
                if row.get("average") is not None
                else self._explicit_nonnegative_float(row, "fill_price", default=None)
                if "fill_price" in row
                else None
            ),
            timestamp=timestamp,
            filled_amount=filled_amount,
            remaining_amount=remaining_amount,
            execution_type_family=execution_type_family(
                order_type,
                provider_type=raw_type,
            ),
            updated_at=self._safe_int(
                row.get("lastTradeTimestamp"),
                self._safe_int(row.get("updated"), timestamp),
            ),
        )

    def _map_trade(self, row: dict[str, object]) -> ExchangeTradeSnapshot | None:
        if not isinstance(row, dict):
            logger.warning("Skipping malformed exchange trade row: %r", row)
            return None
        side = self._order_side(row.get("side"), "trade")
        if side is None:
            return None
        amount = self._optional_nonnegative_float(row.get("amount"))
        price = self._optional_safe_price(row.get("price"))
        symbol = self._from_ccxt_symbol(
            self._provider_string(row, "symbol", "recent-trade")
        ).strip()
        if amount is None or amount <= 0.0 or price is None or price <= 0.0 or not symbol:
            logger.warning(
                "Skipping exchange trade row with invalid critical fields: "
                "symbol=%r amount=%r price=%r",
                row.get("symbol"),
                row.get("amount"),
                row.get("price"),
            )
            return None
        is_derivative = self._default_type != "spot"
        is_inverse = is_derivative and self._position_is_inverse(symbol)
        contract_size = (
            self._position_contract_size(
                symbol,
                row,
                row.get("info") if isinstance(row.get("info"), dict) else {},
                is_inverse=is_inverse,
            )
            if is_derivative
            else None
        )
        contracts = amount if is_derivative else None
        quantity = amount
        quantity_unit = "base"
        if is_derivative:
            quantity = contracts * contract_size
            if is_inverse:
                quantity = contracts
                quantity_unit = "contracts"
            amount = contracts * contract_size
        exchange_trade_id = self._provider_string(row, "id", "recent-trade")
        exchange_order_id = self._provider_string(row, "order", "recent-trade")
        order_id = self._provider_string(row, "orderId", "recent-trade")
        if not exchange_order_id:
            exchange_order_id = order_id
        client_order_id = self._provider_string(row, "clientOrderId", "recent-trade")
        client_oid = self._provider_string(row, "clientOid", "recent-trade")
        if not client_order_id:
            client_order_id = client_oid
        if not exchange_trade_id and not exchange_order_id and not client_order_id:
            logger.warning("Skipping exchange trade row with missing external identity: %r", row)
            return None
        raw_fee = row.get("fee")
        if raw_fee is not None and not isinstance(raw_fee, Mapping):
            raise MalformedProviderData(
                f"Malformed provider trade fee structure: {row!r}"
            )
        fee = 0.0
        if isinstance(raw_fee, Mapping) and raw_fee.get("cost") is not None:
            raw_cost = raw_fee["cost"]
            if isinstance(raw_cost, bool):
                raise MalformedProviderData(f"Malformed provider trade fee cost: {row!r}")
            try:
                fee = float(raw_cost)
            except (TypeError, ValueError, OverflowError) as exc:
                raise MalformedProviderData(
                    f"Malformed provider trade fee cost: {row!r}"
                ) from exc
            if not math.isfinite(fee) or fee < 0.0:
                raise MalformedProviderData(
                    f"Malformed provider trade fee cost: {row!r}"
                )
        return ExchangeTradeSnapshot(
            exchange_trade_id=exchange_trade_id,
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            fee=fee,
            timestamp=self._safe_int(row.get("timestamp")),
            contracts=contracts,
            quantity=quantity,
            quantity_unit=quantity_unit,
            is_inverse=is_inverse,
            contract_size=contract_size,
        )

    def _map_status(
        self,
        status: object,
        filled_amount: float = 0.0,
        amount: float | None = None,
        remaining_amount: float | None = None,
    ) -> OrderStatus:
        normalized_status = str(status or "").lower()
        if (
            normalized_status == "open"
            and amount is not None
            and remaining_amount is not None
            and math.isfinite(remaining_amount)
            and 0.0 <= remaining_amount <= amount
        ):
            filled_amount = amount - remaining_amount
        if normalized_status == "open" and filled_amount > 0:
            if amount is not None and filled_amount >= amount:
                return OrderStatus.FILLED
            return OrderStatus.PARTIALLY_FILLED
        return {
            "open": OrderStatus.PENDING,
            "closed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "cancel": OrderStatus.CANCELLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }.get(normalized_status, OrderStatus.PENDING)
