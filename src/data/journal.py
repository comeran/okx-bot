"""按环境分开的复式账本。净值变动必须等于流水合计。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

ENVIRONMENTS = {"backtest", "paper", "sandbox", "live"}


class JournalError(ValueError):
    pass


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise JournalError(f"{label}必须是精确小数")
    try:
        amount = Decimal(value)
    except Exception as exc:
        raise JournalError(f"{label}必须是精确小数") from exc
    if not amount.is_finite() or amount <= 0:
        raise JournalError(f"{label}必须是正数")
    return amount


@dataclass(frozen=True)
class Posting:
    account: str
    currency: str
    debit: Decimal
    credit: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.debit, Decimal) or not isinstance(self.credit, Decimal):
            raise JournalError("分录金额必须是 Decimal")
        if self.debit < 0 or self.credit < 0:
            raise JournalError("分录金额不能为负")
        if (self.debit > 0) == (self.credit > 0):
            raise JournalError("分录只能记在借方或贷方")


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    ref: str
    timestamp: int
    postings: tuple[Posting, ...]


def _balanced(postings: tuple[Posting, ...] | list[Posting]) -> None:
    totals: dict[str, list[Decimal]] = {}
    for posting in postings:
        totals.setdefault(posting.currency, [Decimal(0), Decimal(0)])
        totals[posting.currency][0] += posting.debit
        totals[posting.currency][1] += posting.credit
    for debit, credit in totals.values():
        if debit != credit:
            raise JournalError("借贷不平衡")


class Journal:
    def __init__(self, environment: str) -> None:
        if environment not in ENVIRONMENTS:
            raise JournalError("环境无效")
        self.environment = environment
        self.entries: list[JournalEntry] = []
        self._quantity: dict[str, Decimal] = {}
        self._cost: dict[str, Decimal] = {}

    @classmethod
    def from_entries(cls, environment: str, entries: list[JournalEntry]) -> Journal:
        book = cls(environment)
        for entry in entries:
            _balanced(entry.postings)
            book.entries.append(entry)
        return book

    def open_cash(self, amount: Decimal, currency: str, timestamp: int) -> None:
        cash = _decimal(amount, "开户金额")
        self._post("open", "open", timestamp, (
            Posting(f"cash:{currency}", currency, cash, Decimal(0)),
            Posting("equity", currency, Decimal(0), cash),
        ))

    def post_trade(
        self,
        *,
        side: str,
        quantity: Decimal,
        price: Decimal,
        currency: str,
        symbol: str,
        ref: str,
        timestamp: int,
    ) -> None:
        qty = _decimal(quantity, "数量")
        px = _decimal(price, "价格")
        notional = qty * px
        if side == "buy":
            self._quantity[symbol] = self._quantity.get(symbol, Decimal(0)) + qty
            self._cost[symbol] = self._cost.get(symbol, Decimal(0)) + notional
            postings = (
                Posting(f"position:{symbol}", currency, notional, Decimal(0)),
                Posting(f"cash:{currency}", currency, Decimal(0), notional),
            )
        elif side == "sell":
            held = self._quantity.get(symbol, Decimal(0))
            if qty > held:
                raise JournalError("持仓不足")
            cost = self._cost[symbol] * qty / held
            pnl = notional - cost
            self._quantity[symbol] = held - qty
            self._cost[symbol] -= cost
            postings_list = [
                Posting(f"cash:{currency}", currency, notional, Decimal(0)),
                Posting(f"position:{symbol}", currency, Decimal(0), cost),
            ]
            if pnl > 0:
                postings_list.append(
                    Posting(f"realized:{symbol}", currency, Decimal(0), pnl)
                )
            elif pnl < 0:
                postings_list.append(
                    Posting(f"realized:{symbol}", currency, -pnl, Decimal(0))
                )
            postings = tuple(postings_list)
        else:
            raise JournalError("方向只能是买入或卖出")
        self._post("trade", ref, timestamp, postings)

    def post_fee(self, amount: Decimal, currency: str, *, ref: str, timestamp: int) -> None:
        fee = _decimal(amount, "手续费")
        self._post("fee", ref, timestamp, (
            Posting(f"fee:{currency}", currency, fee, Decimal(0)),
            Posting(f"cash:{currency}", currency, Decimal(0), fee),
        ))

    def post_funding(self, amount: Decimal, currency: str, *, ref: str, timestamp: int) -> None:
        funding = _decimal(amount, "资金费")
        self._post("funding", ref, timestamp, (
            Posting(f"funding:{currency}", currency, funding, Decimal(0)),
            Posting(f"cash:{currency}", currency, Decimal(0), funding),
        ))

    def post_transfer(
        self, amount: Decimal, currency: str, *, ref: str, timestamp: int, direction: str,
    ) -> None:
        moved = _decimal(amount, "划转")
        if direction == "in":
            postings = (
                Posting(f"cash:{currency}", currency, moved, Decimal(0)),
                Posting(f"transfer:{currency}", currency, Decimal(0), moved),
            )
        elif direction == "out":
            postings = (
                Posting(f"transfer:{currency}", currency, moved, Decimal(0)),
                Posting(f"cash:{currency}", currency, Decimal(0), moved),
            )
        else:
            raise JournalError("划转方向无效")
        self._post("transfer", ref, timestamp, postings)

    def post_settlement(
        self, amount: Decimal, currency: str, *, ref: str, timestamp: int, direction: str,
    ) -> None:
        settled = _decimal(amount, "结算")
        if direction == "receive":
            postings = (
                Posting(f"cash:{currency}", currency, settled, Decimal(0)),
                Posting(f"settlement:{currency}", currency, Decimal(0), settled),
            )
        elif direction == "pay":
            postings = (
                Posting(f"settlement:{currency}", currency, settled, Decimal(0)),
                Posting(f"cash:{currency}", currency, Decimal(0), settled),
            )
        else:
            raise JournalError("结算方向无效")
        self._post("settlement", ref, timestamp, postings)

    def nav(self) -> Decimal:
        return self._net_debit("cash:") + self._net_debit("position:")

    def opening(self) -> Decimal:
        return -self._net_debit("equity")

    def flows(self) -> Decimal:
        return -(
            self._net_debit("realized:")
            + self._net_debit("fee:")
            + self._net_debit("funding:")
            + self._net_debit("transfer:")
            + self._net_debit("settlement:")
        )

    def nav_change(self) -> Decimal:
        return self.nav() - self.opening()

    def _post(
        self, kind: str, ref: str, timestamp: int, postings: tuple[Posting, ...],
    ) -> None:
        _balanced(postings)
        self.entries.append(JournalEntry(kind, ref, timestamp, postings))

    def _net_debit(self, prefix: str) -> Decimal:
        total = Decimal(0)
        for entry in self.entries:
            for posting in entry.postings:
                if posting.account == prefix or posting.account.startswith(prefix):
                    total += posting.debit - posting.credit
        return total


def insert_entry(connection: object, environment: str, seq: int, entry: JournalEntry) -> None:
    if environment not in ENVIRONMENTS:
        raise JournalError("环境无效")
    _balanced(entry.postings)
    for posting in entry.postings:
        connection.execute(text(
            "INSERT INTO journal_posting("
            "environment, entry_seq, kind, entry_ref, account, currency, "
            "debit, credit, created_at"
            ") VALUES ("
            ":environment, :entry_seq, :kind, :entry_ref, :account, :currency, "
            ":debit, :credit, :created_at)"
        ), {
            "environment": environment,
            "entry_seq": seq,
            "kind": entry.kind,
            "entry_ref": entry.ref,
            "account": posting.account,
            "currency": posting.currency,
            "debit": format(posting.debit, "f"),
            "credit": format(posting.credit, "f"),
            "created_at": entry.timestamp,
        })
