from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd


class ICandleRepository(ABC):
    """Репозиторий свечей — CRUD для OHLCV данных."""

    @abstractmethod
    def save_candles(self, symbol: str, timeframe: str, candles: List[tuple]) -> int:
        """Сохранить свечи в БД. Возвращает количество сохранённых."""
        ...

    @abstractmethod
    def load_candles(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Загрузить все свечи для символа и таймфрейма."""
        ...

    @abstractmethod
    def get_last_timestamp(self, symbol: str, timeframe: str) -> Optional[int]:
        """Получить timestamp последней свечи (мс) или None."""
        ...


class ITradeRepository(ABC):
    """Репозиторий сделок — логирование результатов торговли."""

    @abstractmethod
    def save_trade(self, trade: dict) -> None:
        """Сохранить результат сделки."""
        ...

    @abstractmethod
    def load_trades(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Загрузить историю сделок, опционально фильтр по символу."""
        ...