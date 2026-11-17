"""公开行情板：盘口、逐笔、资金费率和持仓量。"""

from __future__ import annotations

from typing import Protocol


class BoardReader(Protocol):
    async def order_book(self, symbol: str, market_type: str) -> dict:
        """返回买卖盘。"""

    async def trades(self, symbol: str, market_type: str) -> list:
        """返回最近逐笔。"""

    async def funding(self, symbol: str, market_type: str) -> dict:
        """返回资金费率。"""

    async def open_interest(self, symbol: str, market_type: str) -> dict:
        """返回持仓量。"""


def _absent() -> dict[str, object]:
    return {"available": False, "reason": "not_applicable"}


async def load_market_board(
    symbol: str, market_type: str, reader: BoardReader,
) -> dict[str, object]:
    """现货没有资金费率和持仓量。合约才向交易所读取这两项。"""
    book = await reader.order_book(symbol, market_type)
    trades = await reader.trades(symbol, market_type)
    if market_type == "spot":
        funding = _absent()
        interest = _absent()
    else:
        funding = await reader.funding(symbol, market_type)
        interest = await reader.open_interest(symbol, market_type)
    return {
        "symbol": symbol,
        "market_type": market_type,
        "order_book": book,
        "trades": trades,
        "funding_rate": funding,
        "open_interest": interest,
    }


def ccxt_symbol(symbol: str, market_type: str) -> str:
    text = symbol.strip().upper()
    if market_type == "swap" and text.endswith("-SWAP"):
        base, quote, _swap = text.split("-")
        return f"{base}/{quote}:{quote}"
    if market_type == "future":
        base, quote, expiry = text.split("-")
        return f"{base}/{quote}:{quote}-{expiry}"
    return text.replace("-", "/")


class CcxtBoardReader:
    """用公开行情接口读取盘口。不下单。"""

    async def order_book(self, symbol: str, market_type: str) -> dict:
        client = await self._client(market_type)
        try:
            book = await client.fetch_order_book(ccxt_symbol(symbol, market_type), 10)
            return {"bids": book.get("bids", [])[:10], "asks": book.get("asks", [])[:10]}
        finally:
            await client.close()

    async def trades(self, symbol: str, market_type: str) -> list:
        client = await self._client(market_type)
        try:
            rows = await client.fetch_trades(ccxt_symbol(symbol, market_type), limit=20)
            return [
                {
                    "price": row.get("price"),
                    "amount": row.get("amount"),
                    "side": row.get("side"),
                    "timestamp": row.get("timestamp"),
                }
                for row in rows
            ]
        finally:
            await client.close()

    async def funding(self, symbol: str, market_type: str) -> dict:
        client = await self._client(market_type)
        try:
            row = await client.fetch_funding_rate(ccxt_symbol(symbol, market_type))
            return {"available": True, "rate": row.get("fundingRate")}
        finally:
            await client.close()

    async def open_interest(self, symbol: str, market_type: str) -> dict:
        client = await self._client(market_type)
        try:
            row = await client.fetch_open_interest(ccxt_symbol(symbol, market_type))
            amount = row.get("openInterestAmount", row.get("openInterest"))
            return {"available": True, "amount": amount}
        finally:
            await client.close()

    async def _client(self, market_type: str):
        import ccxt.async_support as ccxt

        default_type = "swap" if market_type in {"swap", "future", "option"} else "spot"
        return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": default_type}})
