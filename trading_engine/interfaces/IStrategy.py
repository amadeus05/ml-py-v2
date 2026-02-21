from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from trading_engine.core.domain.models import Signal


class IStrategy(ABC):
    """
    Стратегия — генерация торговых сигналов.
    Объединяет FeatureEngine + Model в единый пайплайн.
    """

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, htf_df: Optional[pd.DataFrame] = None
    ) -> Optional[Signal]:
        """
        Генерация сигнала из рыночных данных.
        
        Args:
            df: OHLCV данные основного таймфрейма (с достаточной историей для фичей)
            htf_df: OHLCV данные старшего таймфрейма (опционально)
        
        Returns:
            Signal или None если нет сигнала
        """
        ...
