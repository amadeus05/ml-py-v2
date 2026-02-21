import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def triple_barrier_labeling(
    df: pd.DataFrame,
    df_1m: pd.DataFrame,
    horizon: int,
    tp_pct: float,
    sl_pct: float,
    timeframe: str,
) -> pd.DataFrame:
    """
    Разметка данных (Teacher) — 100% калька MVP etl_pipeline.py triple_barrier_labeling().
    
    Барьеры = фиксированные TP_PCT / SL_PCT.
    Использует 1m данные для точного определения что сработало первым (TP или SL).
    """
    from trading_engine.core.domain.enums import Timeframe

    labels = []
    closes = df["close"].values
    timestamps = df["timestamp"].values

    # Предварительно индексируем 1m данные
    df_1m_sorted = df_1m.sort_values("timestamp").reset_index(drop=True)
    m1_timestamps = df_1m_sorted["timestamp"].values
    m1_highs = df_1m_sorted["high"].values
    m1_lows = df_1m_sorted["low"].values

    # Интервал основного TF в наносекундах
    tf_ms_map = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                 "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    tf_ms = tf_ms_map.get(timeframe, 3_600_000)
    tf_ns = np.timedelta64(tf_ms, "ms")

    ambiguous_count = 0
    total_labeled = 0

    for i in range(len(df) - horizon):
        current_price = closes[i]
        upper_barrier = current_price * (1 + tp_pct)
        lower_barrier = current_price * (1 - sl_pct)

        # Окно: от следующей свечи до +HORIZON свечей
        window_start = timestamps[i] + tf_ns
        window_end = timestamps[i] + tf_ns * (horizon + 1)

        # Бинарный поиск 1m свечи в окне
        idx_start = np.searchsorted(m1_timestamps, window_start, side="left")
        idx_end = np.searchsorted(m1_timestamps, window_end, side="left")

        label = 0

        # Хронологический проход по 1m свечам
        for k in range(idx_start, idx_end):
            m1_low = m1_lows[k]
            m1_high = m1_highs[k]

            hit_sl = m1_low <= lower_barrier
            hit_tp = m1_high >= upper_barrier

            if hit_sl and hit_tp:
                # На 1m крайне редко, но SL приоритетнее (консервативно)
                ambiguous_count += 1
                label = -1
                break
            elif hit_sl:
                label = -1
                break
            elif hit_tp:
                label = 1
                break

        labels.append(label)
        total_labeled += 1

    if ambiguous_count > 0:
        logger.warning(
            f"⚠️ Ambiguous 1m candles: {ambiguous_count}/{total_labeled} "
            f"({ambiguous_count / total_labeled * 100:.2f}%)"
        )
    else:
        logger.info(f"✅ No ambiguous 1m candles out of {total_labeled}")

    labels.extend([0] * horizon)
    df = df.copy()
    df["Target"] = labels
    return df


def add_mfe_targets(
    df: pd.DataFrame,
    df_1m: pd.DataFrame,
    horizon: int,
    timeframe: str,
) -> pd.DataFrame:
    """
    MFE таргеты — 100% калька MVP etl_pipeline.py add_mfe_targets().
    
    MFE_long  = max % движение вверх за HORIZON свечей
    MFE_short = max % движение вниз за HORIZON свечей
    Использует 1m данные для точного расчёта.
    """
    closes = df["close"].values
    timestamps = df["timestamp"].values

    df_1m_sorted = df_1m.sort_values("timestamp").reset_index(drop=True)
    m1_timestamps = df_1m_sorted["timestamp"].values
    m1_highs = df_1m_sorted["high"].values
    m1_lows = df_1m_sorted["low"].values

    tf_ms_map = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                 "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    tf_ms = tf_ms_map.get(timeframe, 3_600_000)
    tf_ns = np.timedelta64(tf_ms, "ms")

    mfe_long = []
    mfe_short = []

    for i in range(len(df) - horizon):
        window_start = timestamps[i] + tf_ns
        window_end = timestamps[i] + tf_ns * (horizon + 1)

        idx_start = np.searchsorted(m1_timestamps, window_start, side="left")
        idx_end = np.searchsorted(m1_timestamps, window_end, side="left")

        if idx_start < idx_end:
            future_highs = m1_highs[idx_start:idx_end]
            future_lows = m1_lows[idx_start:idx_end]
            mfe_long.append(future_highs.max() / closes[i] - 1)
            mfe_short.append(1 - future_lows.min() / closes[i])
        else:
            mfe_long.append(0.0)
            mfe_short.append(0.0)

    # Последние HORIZON строк — фейковые
    mfe_long.extend([0.0] * horizon)
    mfe_short.extend([0.0] * horizon)

    df = df.copy()
    df["MFE_long"] = mfe_long
    df["MFE_short"] = mfe_short
    return df
