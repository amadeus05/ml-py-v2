import logging
from typing import Optional

import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class PaperExchange(IExchange):
    """
    Paper trading exchange — использует реальные данные Binance API
    для fetch_ohlcv, но ордера исполняет локально (симуляция fill).
    
    Slippage применяется при fill — как в MVP bt.py.
    """

    def __init__(self, settings: Settings, data_exchange: IExchange, initial_balance: float = 100.0):
        """
        Args:
            settings: Config settings
            data_exchange: Реальная биржа для получения данных (BinanceExchange)
            initial_balance: Стартовый баланс
        """
        self.settings = settings
        self.data_exchange = data_exchange
        self._balance = initial_balance
        self._last_prices: dict[str, float] = {}

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """Использует реальную биржу для данных."""
        df = await self.data_exchange.fetch_ohlcv(symbol, timeframe, since, limit)
        if not df.empty and "close" in df.columns:
            self._last_prices[symbol] = df["close"].iloc[-1]
        return df

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
        reduce_only: bool = False,
    ) -> dict:
        """
        Симуляция ордера: fill по текущей цене + slippage.
        Slippage как в MVP bt.py lines 250/254.
        """
        slippage = self.settings.SLIPPAGE
        
        if side == "BUY":
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        commission = quantity * fill_price * self.settings.TAKER_FEE

        logger.info(
            f"[PAPER] {side} {symbol} qty={quantity:.6f} "
            f"price={price:.2f} → fill={fill_price:.2f} comm={commission:.4f}"
        )

        return {
            "fill_price": fill_price,
            "fill_quantity": quantity,
            "commission": commission,
            "exchange_order_id": f"PAPER-{symbol}-{side}",
        }

    async def get_balance(self) -> float:
        return self._balance

    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """Для paper-режима биржевые позиции не используются."""
        return []

    def set_balance(self, balance: float) -> None:
        """Установить начальный баланс для paper trading."""
        self._balance = balance

    async def close(self) -> None:
        await self.data_exchange.close()