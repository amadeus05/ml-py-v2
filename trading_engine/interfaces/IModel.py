from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from trading_engine.core.domain.models import Signal


class IModel(ABC):
    """
    ML модель — загрузка и предсказание.
    Инкапсулирует классификатор + MFE регрессоры.
    """

    @abstractmethod
    def load(self, models_dir: str) -> None:
        """Загрузить модели с диска."""
        ...

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> Optional[Signal]:
        """
        Сгенерировать сигнал из фичей.
        
        Логика (100% MVP bt.py lines 207-229):
            1. predict_proba → p_short, p_neutral, p_long
            2. if p_long > threshold → LONG signal
            3. if p_short > threshold → SHORT signal
            4. MFE filter: predicted_mfe >= MFE_THRESHOLD
        
        Returns:
            Signal или None если нет сигнала
        """
        ...

    @abstractmethod
    def get_feature_names(self) -> list:
        """Список имён фичей (из features.pkl)."""
        ...