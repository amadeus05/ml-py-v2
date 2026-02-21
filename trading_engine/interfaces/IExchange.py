from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd


class IExchange(ABC):
    """Интерфейс биржи — реализуется BinanceExchange, PaperExchange, SimulationExchange."""

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """
        Загрузить OHLCV свечи.
        
        Args:
            symbol: "ETH/USDT"
            timeframe: "1h", "4h", etc.
            since: Timestamp в мс (начало)
            limit: Количество свечей за запрос
        
        Returns:
            DataFrame с колонками: timestamp, open, high, low, close, volume
        """
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
    ) -> dict:
        """
        Разместить ордер на бирже.
        
        Returns:
            dict с информацией о fill (fill_price, fill_quantity, commission, exchange_order_id)
        """
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """Получить текущий баланс USDT."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Закрыть соединение."""
        ...