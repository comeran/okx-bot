from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from src.data.models import (
    AccountRecord,
    BacktestResultRecord,
    CashLedgerRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    TradeRecord,
)


class Repository:
    def __init__(self, engine: Engine | None = None, db_path: str = "data/bot.db"):
        if engine is None:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            self._enable_wal(engine)
            SQLModel.metadata.create_all(engine)
            self._migrate_sqlite_schema(engine)
        self.engine = engine

    def upsert_account(self, account: AccountRecord) -> AccountRecord:
        with Session(self.engine) as session:
            existing = session.exec(
                select(AccountRecord).where(AccountRecord.strategy == account.strategy)
            ).first()
            if existing is None:
                session.add(account)
                session.commit()
                session.refresh(account)
                return account

            existing.initial_equity = account.initial_equity
            existing.cash_balance = account.cash_balance
            existing.equity = account.equity
            existing.realized_pnl = account.realized_pnl
            existing.unrealized_pnl = account.unrealized_pnl
            existing.daily_pnl = account.daily_pnl
            existing.fees_paid = account.fees_paid
            existing.updated_at = account.updated_at
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    save_account = upsert_account

    def get_account(self, strategy: str | None = None) -> AccountRecord | None:
        with Session(self.engine) as session:
            if strategy is not None:
                return session.exec(
                    select(AccountRecord).where(AccountRecord.strategy == strategy)
                ).first()

            accounts = list(session.exec(select(AccountRecord)).all())
            if not accounts:
                return None
            if len(accounts) == 1:
                return accounts[0]
            return AccountRecord(
                strategy="",
                initial_equity=sum(account.initial_equity for account in accounts),
                cash_balance=sum(account.cash_balance for account in accounts),
                equity=sum(account.equity for account in accounts),
                realized_pnl=sum(account.realized_pnl for account in accounts),
                unrealized_pnl=sum(account.unrealized_pnl for account in accounts),
                daily_pnl=sum(account.daily_pnl for account in accounts),
                fees_paid=sum(account.fees_paid for account in accounts),
                updated_at=max(account.updated_at for account in accounts),
            )

    def save_cash_ledger(self, entry: CashLedgerRecord) -> CashLedgerRecord:
        with Session(self.engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    save_cash_ledger_entry = save_cash_ledger

    def get_cash_ledger(self, strategy: str | None = None) -> list[CashLedgerRecord]:
        statement = select(CashLedgerRecord)
        if strategy is not None:
            statement = statement.where(CashLedgerRecord.strategy == strategy)
        statement = statement.order_by(CashLedgerRecord.timestamp)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_trade(self, trade: TradeRecord) -> TradeRecord:
        with Session(self.engine) as session:
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade

    def get_trades(self, strategy: str | None = None) -> list[TradeRecord]:
        statement = select(TradeRecord)
        if strategy is not None:
            statement = statement.where(TradeRecord.strategy == strategy)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_order(self, order: OrderRecord) -> OrderRecord:
        with Session(self.engine) as session:
            session.add(order)
            session.commit()
            session.refresh(order)
            return order

    def get_orders(self, order_id: str | None = None) -> list[OrderRecord]:
        statement = select(OrderRecord)
        if order_id is not None:
            statement = statement.where(OrderRecord.order_id == order_id)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_position(self, position: PositionRecord) -> PositionRecord:
        with Session(self.engine) as session:
            session.add(position)
            session.commit()
            session.refresh(position)
            return position

    def get_position(self, strategy: str, symbol: str) -> PositionRecord | None:
        statement = select(PositionRecord).where(
            PositionRecord.strategy == strategy,
            PositionRecord.symbol == symbol,
        )
        with Session(self.engine) as session:
            return session.exec(statement).first()

    def upsert_position(self, position: PositionRecord) -> PositionRecord:
        with Session(self.engine) as session:
            existing = session.exec(
                select(PositionRecord).where(
                    PositionRecord.strategy == position.strategy,
                    PositionRecord.symbol == position.symbol,
                )
            ).first()
            if existing is None:
                session.add(position)
                session.commit()
                session.refresh(position)
                return position

            existing.side = position.side
            existing.amount = position.amount
            existing.entry_price = position.entry_price
            existing.leverage = position.leverage
            existing.timestamp = position.timestamp
            existing.mark_price = position.mark_price
            existing.realized_pnl = position.realized_pnl
            existing.unrealized_pnl = position.unrealized_pnl
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def delete_position(self, strategy: str, symbol: str) -> None:
        with Session(self.engine) as session:
            position = session.exec(
                select(PositionRecord).where(
                    PositionRecord.strategy == strategy,
                    PositionRecord.symbol == symbol,
                )
            ).first()
            if position is not None:
                session.delete(position)
                session.commit()

    def get_positions(self, strategy: str | None = None) -> list[PositionRecord]:
        statement = select(PositionRecord)
        if strategy is not None:
            statement = statement.where(PositionRecord.strategy == strategy)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def get_open_positions(self, strategy: str | None = None) -> list[PositionRecord]:
        statement = select(PositionRecord).where(PositionRecord.amount != 0)
        if strategy is not None:
            statement = statement.where(PositionRecord.strategy == strategy)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_kline(self, kline: KlineCache) -> KlineCache:
        with Session(self.engine) as session:
            session.add(kline)
            session.commit()
            session.refresh(kline)
            return kline

    def get_klines(self, symbol: str, timeframe: str, start: int, end: int) -> list[KlineCache]:
        statement = (
            select(KlineCache)
            .where(KlineCache.symbol == symbol)
            .where(KlineCache.timeframe == timeframe)
            .where(KlineCache.timestamp >= start)
            .where(KlineCache.timestamp <= end)
            .order_by(KlineCache.timestamp)
        )
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_backtest_result(
        self, result: BacktestResultRecord
    ) -> BacktestResultRecord:
        with Session(self.engine) as session:
            session.add(result)
            session.commit()
            session.refresh(result)
            return result

    def get_backtest_results(self, limit: int = 50) -> list[BacktestResultRecord]:
        statement = (
            select(BacktestResultRecord)
            .order_by(BacktestResultRecord.created_at.desc())
            .limit(limit)
        )
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    @staticmethod
    def _enable_wal(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    @staticmethod
    def _migrate_sqlite_schema(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return
        with engine.begin() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(positionrecord)"))
            }
            if not columns:
                return
            migrations = {
                "mark_price": "ALTER TABLE positionrecord ADD COLUMN mark_price FLOAT",
                "realized_pnl": "ALTER TABLE positionrecord ADD COLUMN realized_pnl FLOAT DEFAULT 0.0",
                "unrealized_pnl": "ALTER TABLE positionrecord ADD COLUMN unrealized_pnl FLOAT DEFAULT 0.0",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))
