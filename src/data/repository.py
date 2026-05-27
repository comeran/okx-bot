from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from src.data.models import KlineCache, OrderRecord, PositionRecord, TradeRecord


class Repository:
    def __init__(self, engine: Engine | None = None, db_path: str = "data/bot.db"):
        if engine is None:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            self._enable_wal(engine)
            SQLModel.metadata.create_all(engine)
        self.engine = engine

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

    def get_positions(self, strategy: str | None = None) -> list[PositionRecord]:
        statement = select(PositionRecord)
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

    @staticmethod
    def _enable_wal(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
