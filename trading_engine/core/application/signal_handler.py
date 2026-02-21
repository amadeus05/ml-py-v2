import logging
from typing import Optional

import pandas as pd

from trading_engine.interfaces.IFeatureEngine import IFeatureEngine
from trading_engine.interfaces.IModel import IModel
from trading_engine.core.domain.models import Signal

logger = logging.getLogger(__name__)


class SignalHandler:
    """
    ML Pipeline: Features → Model → Signal.
    
    Объединяет FeatureEngine и Model в единый flow.
    """

    def __init__(self, feature_engine: IFeatureEngine, model: IModel):
        self.feature_engine = feature_engine
        self.model = model

    def process(
        self,
        df: pd.DataFrame,
        htf_df: Optional[pd.DataFrame] = None,
        symbol: str = "",
        row_index: int = -1,
    ) -> Optional[Signal]:
        """
        Генерация сигнала из рыночных данных.
        
        Args:
            df: OHLCV DataFrame основного TF (уже с фичами, если бэктест)
            htf_df: HTF DataFrame (опционально)
            symbol: Символ для Signal
            row_index: Индекс строки для predict (default: последняя)
        
        Returns:
            Signal или None
        """
        # Если фичи ещё не сгенерированы — генерируем
        feature_names = self.model.get_feature_names()
        if not all(f in df.columns for f in feature_names):
            df = self.feature_engine.transform(df, htf_df=htf_df)

        if df.empty:
            return None

        # Берём нужную строку
        if row_index >= 0:
            row_df = df.iloc[[row_index]]
        else:
            row_df = df.iloc[[-1]]

        # Добавляем symbol в DataFrame для IModel
        row_df = row_df.copy()
        row_df["symbol"] = symbol

        signal = self.model.predict(row_df)
        return signal