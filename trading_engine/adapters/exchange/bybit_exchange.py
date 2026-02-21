from trading_engine.interfaces.IExchange import IExchange


class BybitExchange(IExchange):
    """
    Bybit exchange adapter — stub.
    Будет реализован в следующей версии.
    """

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1500):
        raise NotImplementedError("BybitExchange not implemented yet")

    async def place_order(self, symbol, side, quantity, price, order_type="MARKET"):
        raise NotImplementedError("BybitExchange not implemented yet")

    async def get_balance(self):
        raise NotImplementedError("BybitExchange not implemented yet")

    async def close(self):
        pass
