import logging
from typing import List, Optional

import pandas as pd

from trading_engine.interfaces.IRepository import ICandleRepository
from trading_engine.adapters.persistence.database import DatabaseManager

logger = logging.getLogger(__name__)


class SQLiteCandleRepository(ICandleRepository):
    """
    CRUD для OHLCV свечей в SQLite.
    SQL запросы идентичны MVP etl_pipeline.py.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def save_candles(self, symbol: str, timeframe: str, candles: List[tuple]) -> int:
        """
        Сохранить свечи — как MVP fetch_data() line:
            cur.executemany("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?,?)", rows)
        
        Args:
            candles: list of tuples (symbol, timeframe, open_time, o, h, l, c, vol, quote_vol)
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                candles,
            )
            conn.commit()
            return len(candles)

    def load_candles(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        Загрузка из БД — 100% как MVP load_from_db():
            SELECT open_time as timestamp, open, high, low, close, volume
            FROM candles WHERE symbol=? AND timeframe=? ORDER BY open_time
        """
        conn = self.db.get_raw_connection()
        try:
            df = pd.read_sql_query(
                "SELECT open_time as timestamp, open, high, low, close, volume "
                "FROM candles WHERE symbol=? AND timeframe=? ORDER BY open_time",
                conn,
                params=(symbol, timeframe),
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        finally:
            conn.close()

    def get_last_timestamp(self, symbol: str, timeframe: str) -> Optional[int]:
        """
        Последний timestamp — как MVP fetch_data():
            SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe=?
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT MAX(open_time) FROM candles WHERE symbol=? AND timeframe=?",
                (symbol, timeframe),
            )
            result = cur.fetchone()[0]
            return result

    def save_features(self, df: pd.DataFrame, symbol: str) -> None:
        """
        Сохранение обработанных данных — как MVP save_processed():
            table_name = symbol.replace('/', '_') + "_features"
            df.to_sql(table_name, conn, if_exists='replace', index=False)
        """
        conn = self.db.get_raw_connection()
        try:
            table_name = symbol.replace("/", "_") + "_features"
            df.to_sql(table_name, conn, if_exists="replace", index=False)
            logger.info(f"💾 {symbol} features saved ({len(df)} rows)")
        finally:
            conn.close()

    def load_features(self, symbol: str, feature_names: Optional[List[str]] = None) -> pd.DataFrame:
        """Загрузка features таблицы (для бэктеста / инференса)."""
        conn = self.db.get_raw_connection()
        try:
            table_name = symbol.replace("/", "_") + "_features"
            df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
            if feature_names:
                cols_to_keep = ["timestamp", "open", "high", "low", "close"] + feature_names
                available_cols = [c for c in cols_to_keep if c in df.columns]
                df = df[available_cols]
            return df
        finally:
            conn.close()

    def load_all_features(self) -> pd.DataFrame:
        """
        Загрузка всех features таблиц — как MVP train.py load_data_from_db():
            SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_features'
        """
        conn = self.db.get_raw_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_features';"
            )
            tables = cursor.fetchall()

            all_data = []
            for (table_name,) in tables:
                df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
                all_data.append(df)

            if not all_data:
                return pd.DataFrame()

            return pd.concat(all_data, ignore_index=True)
        finally:
            conn.close()
