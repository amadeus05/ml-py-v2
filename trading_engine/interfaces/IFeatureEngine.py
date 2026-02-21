from abc import ABC, abstractmethod

import pandas as pd


class IFeatureEngine(ABC):
    """
    Формирование фичей из raw market data.
    100% точность расчётов как в MVP etl_pipeline.py.
    """

    @abstractmethod
    def transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Принимает raw market dataframe (timestamp, open, high, low, close, volume).
        Возвращает dataframe с фичами.
        Дополнительный контекст (например, htf_df) может быть передан через kwargs.
        """
        ...