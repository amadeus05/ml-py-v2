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
        reduce_only: bool = False,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
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
    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """
        Получить позиции с биржи (например, Binance positionRisk).
        Возвращает список словарей с полями: symbol, quantity, entry_price, leverage, mark_price.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Закрыть соединение."""
        ...

    # @abstractmethod
    # async def set_leverage(self, symbol: str, leverage: int) -> None:
    #     """Установить кредитное плечо для символа."""
    #     ...
    
    # @abstractmethod
    # async def get_positions(self) -> int:
    #     """Получить количество открытых позиций."""
    #     ...
    
    # @abstractmethod
    # async def get_funding_rate(self, symbol: str) -> float:
    #     """Получить funding rate для символа."""
    #     ...

    # @abstractmethod
    # async def get_ohlc_in_interval(self, symbol: str, start_time: int, end_time: int, limit: int) -> pd.DataFrame:
    #     """Получить OHLCV свечи в интервале."""
    #     ...