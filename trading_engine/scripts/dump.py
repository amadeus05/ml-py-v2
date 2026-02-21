"""
ETL Runner — загрузка данных + генерация фичей + лейблинг.
100% логика из MVP etl_pipeline.py main().
"""
import logging
import asyncio

from trading_engine.adapters.config.settings import Settings
from trading_engine.adapters.persistence.database import DatabaseManager
from trading_engine.adapters.persistence.repositories.candle_repository import SQLiteCandleRepository
from trading_engine.adapters.exchange.binance_exchange import BinanceExchange
from trading_engine.adapters.ml.feature_engine import CryptoFeatureEngine
from trading_engine.adapters.ml.labeling import triple_barrier_labeling, add_mfe_targets


logger = logging.getLogger(__name__)


async def run_etl(settings: Settings = None):
    """
    ETL Pipeline:
    1. Инкрементальная загрузка свечей с Binance (основной TF + HTF + 1m)
    2. Генерация фичей (CryptoFeatureEngine)
    3. Triple barrier labeling
    4. MFE targets
    5. Сохранение в SQLite (features таблица)
    
    100% как MVP etl_pipeline.py main()
    """
    if settings is None:
        settings = Settings()

    db = DatabaseManager(settings.DB_PATH)
    candle_repo = SQLiteCandleRepository(db)
    exchange = BinanceExchange(settings)
    feature_engine = CryptoFeatureEngine()

    for symbol in settings.SYMBOLS:
        # --- 1. Загрузка основного таймфрейма ---
        logger.info(f"Loading {symbol} {settings.TIMEFRAME} from {settings.START_DATE}...")
        last_ts = candle_repo.get_last_timestamp(symbol, settings.TIMEFRAME)
        since = last_ts + 1 if last_ts else None
        df_new = await exchange.fetch_ohlcv(symbol, settings.TIMEFRAME, since=since)
        if not df_new.empty:
            rows = [
                (symbol, settings.TIMEFRAME, int(r["open_time_ms"]),
                 r["open"], r["high"], r["low"], r["close"], r["volume"], r["quote_volume"])
                for _, r in df_new.iterrows()
            ]
            loaded = candle_repo.save_candles(symbol, settings.TIMEFRAME, rows)
            logger.info(f"{symbol} {settings.TIMEFRAME}: {loaded} new candles")

        # --- 2. Загрузка HTF ---
        logger.info(f"Loading {symbol} {settings.HTF_TIMEFRAME} from {settings.START_DATE}...")
        last_ts = candle_repo.get_last_timestamp(symbol, settings.HTF_TIMEFRAME)
        since = last_ts + 1 if last_ts else None
        df_htf_new = await exchange.fetch_ohlcv(symbol, settings.HTF_TIMEFRAME, since=since)
        if not df_htf_new.empty:
            rows = [
                (symbol, settings.HTF_TIMEFRAME, int(r["open_time_ms"]),
                 r["open"], r["high"], r["low"], r["close"], r["volume"], r["quote_volume"])
                for _, r in df_htf_new.iterrows()
            ]
            candle_repo.save_candles(symbol, settings.HTF_TIMEFRAME, rows)

        # --- 3. Загрузка 1m для лейблинга ---
        logger.info(f"Loading {symbol} {settings.LABEL_TIMEFRAME} for labeling...")
        last_ts = candle_repo.get_last_timestamp(symbol, settings.LABEL_TIMEFRAME)
        since = last_ts + 1 if last_ts else None
        df_1m_new = await exchange.fetch_ohlcv(symbol, settings.LABEL_TIMEFRAME, since=since)
        if not df_1m_new.empty:
            rows = [
                (symbol, settings.LABEL_TIMEFRAME, int(r["open_time_ms"]),
                 r["open"], r["high"], r["low"], r["close"], r["volume"], r["quote_volume"])
                for _, r in df_1m_new.iterrows()
            ]
            candle_repo.save_candles(symbol, settings.LABEL_TIMEFRAME, rows)

        # --- 4. Загрузка из БД ---
        df = candle_repo.load_candles(symbol, settings.TIMEFRAME)
        htf_df = candle_repo.load_candles(symbol, settings.HTF_TIMEFRAME)
        df_1m = candle_repo.load_candles(symbol, settings.LABEL_TIMEFRAME)

        if len(df) > 0 and len(htf_df) > 0 and len(df_1m) > 0:
            logger.info(f"{symbol}: 1h={len(df)}, 4h={len(htf_df)}, 1m={len(df_1m)} candles")

            # --- 5. Feature Engineering ---
            df = feature_engine.transform(df, htf_df=htf_df)

            # --- 6. Triple Barrier Labeling ---
            df = triple_barrier_labeling(
                df, df_1m,
                horizon=settings.HORIZON,
                tp_pct=settings.TP_PCT,
                sl_pct=settings.SL_PCT,
                timeframe=settings.TIMEFRAME,
            )

            # --- 7. MFE Targets ---
            df = add_mfe_targets(
                df, df_1m,
                horizon=settings.HORIZON,
                timeframe=settings.TIMEFRAME,
            )

            # --- 8. Save ---
            candle_repo.save_features(df, symbol)
            logger.info(f"{symbol}: saved {len(df)} rows with features + labels")
        else:
            logger.warning(
                f"{symbol}: not enough data (1h={len(df)}, 4h={len(htf_df)}, 1m={len(df_1m)})"
            )

    await exchange.close()
    logger.info("ETL pipeline complete!")


if __name__ == "__main__":
    asyncio.run(run_etl())
