import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

from trading_engine.interfaces.IFeatureEngine import IFeatureEngine

logger = logging.getLogger(__name__)


class CryptoFeatureEngine(IFeatureEngine):
    """
    Формирование фичей — 100% калька MVP etl_pipeline.py add_features() + add_htf_features().
    
    КАЖДАЯ строка расчёта идентична MVP. Порядок фичей сохранён.
    """

    def transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Полный пайплайн фичей.
        
        Args:
            df: OHLCV DataFrame основного таймфрейма (timestamp, open, high, low, close, volume)
            kwargs:
                htf_df: DataFrame старшего таймфрейма (опционально)
        
        Returns:
            DataFrame с фичами (без NaN, dropna применён)
        """
        df = self._add_features(df)

        htf_df = kwargs.get("htf_df")
        if htf_df is not None and not htf_df.empty:
            df = self._add_htf_features(df, htf_df)

        return df

    def _add_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        100% калька MVP etl_pipeline.py add_features().
        Генерация признаков БЕЗ подсматривания в будущее.
        """
        df = df.copy()

        # 1. Трендовые и Осцилляторы (ТЕКУЩИЕ, без shift)
        df["RSI"] = df.ta.rsi(length=14)
        macd = df.ta.macd()
        df["MACD_line"] = macd["MACD_12_26_9"]
        df["MACD_signal"] = macd["MACDs_12_26_9"]
        df["MACD_hist"] = macd["MACDh_12_26_9"]
        df["ATR"] = df.ta.atr(length=14)

        # 2. Логарифмическая доходность (Текущая Close к Прошлой Close)
        df["Log_Ret"] = np.log(df["close"] / df["close"].shift(1))

        # 3. Относительный объем
        # Используем скользящее среднее текущего момента (включая текущий бар)
        df["volume_ma_20"] = df["volume"].rolling(20, min_periods=1).mean()
        df["Vol_Rel"] = df["volume"] / df["volume_ma_20"]

        # 4. Лаги (для истории)
        for col in ["RSI", "Log_Ret", "Vol_Rel"]:
            for i in range(1, 4):
                df[f"{col}_lag_{i}"] = df[col].shift(i)

        # 5. Время
        df["hour_sin"] = np.sin(2 * np.pi * df["timestamp"].dt.hour / 24)
        df["day_of_week"] = df["timestamp"].dt.dayofweek

        # 6. EMA
        df["EMA_200"] = df["close"].ewm(span=200, adjust=False).mean()
        df["Trend"] = (df["close"] > df["EMA_200"]).astype(int)

        # 6b. Regime features
        adx = df.ta.adx(length=14)
        df["ADX"] = adx["ADX_14"]
        df["EMA_slope_20"] = df["EMA_200"].pct_change(20)
        df["Volatility_20"] = df["Log_Ret"].rolling(20).std()

        # 7. Поддержка / Сопротивление
        SR_LOOKBACK = 50
        # Уровни строим по ПРОШЛЫМ данным (shift(1) ОБЯЗАТЕЛЕН для уровней)
        df["Resistance"] = df["high"].rolling(SR_LOOKBACK, min_periods=1).max().shift(1)
        df["Support"] = df["low"].rolling(SR_LOOKBACK, min_periods=1).min().shift(1)

        # Дистанцию считаем от ТЕКУЩЕЙ цены до уровней
        df["Dist_to_Resistance"] = (df["Resistance"] - df["close"]) / df["ATR"]
        df["Dist_to_Support"] = (df["close"] - df["Support"]) / df["ATR"]

        # Позиция цены: считаем по текущей цене
        sr_range = df["Resistance"] - df["Support"]
        df["SR_Position"] = ((df["close"] - df["Support"]) / sr_range).clip(0, 1)

        df.dropna(inplace=True)
        return df

    def _add_htf_features(self, df: pd.DataFrame, htf_df: pd.DataFrame) -> pd.DataFrame:
        """
        100% калька MVP etl_pipeline.py add_htf_features().
        
        ВАЖНО: shift(1) для HTF — timestamps это Open Time.
        Без shift(1) мы бы заглянули в 'будущее' (в конец 4h свечи) при merge_asof.
        """
        htf = htf_df.copy()

        # Считаем индикаторы на 4h (shift(1) — только ЗАВЕРШЕННЫЕ свечи)
        htf["HTF_RSI"] = htf.ta.rsi(length=14).shift(1)
        htf["HTF_ATR"] = htf.ta.atr(length=14).shift(1)
        htf_macd = htf.ta.macd()
        htf["HTF_MACD_hist"] = htf_macd["MACDh_12_26_9"].shift(1)
        htf["HTF_EMA_50"] = htf["close"].ewm(span=50, adjust=False).mean().shift(1)
        htf["HTF_Trend"] = (htf["close"].shift(1) > htf["HTF_EMA_50"]).astype(int)
        htf["HTF_Log_Ret"] = np.log(htf["close"] / htf["close"].shift(1))

        # Только нужные колонки для merge
        htf_cols = [
            "timestamp", "HTF_RSI", "HTF_ATR", "HTF_MACD_hist",
            "HTF_EMA_50", "HTF_Trend", "HTF_Log_Ret",
        ]
        htf = htf[htf_cols].dropna()

        # merge_asof: для каждого 1h timestamp берем последнюю 4h запись <= этого времени
        df = df.sort_values("timestamp")
        htf = htf.sort_values("timestamp")
        df = pd.merge_asof(df, htf, on="timestamp", direction="backward")

        df.dropna(inplace=True)
        return df
