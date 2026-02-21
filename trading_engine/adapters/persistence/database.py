import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    SQLite connection manager.
    
    Схема таблицы candles — 100% как MVP etl_pipeline.py init_db().
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _init_tables(self):
        """Создание таблиц если не существуют — как MVP init_db()."""
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT,
                    timeframe TEXT,
                    open_time INTEGER,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    quote_volume REAL,
                    PRIMARY KEY (symbol, timeframe, open_time)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    notional REAL NOT NULL,
                    margin REAL NOT NULL,
                    raw_pnl_pct REAL NOT NULL,
                    net_pnl_pct REAL NOT NULL,
                    pnl_abs REAL NOT NULL,
                    commission REAL NOT NULL,
                    reason TEXT NOT NULL,
                    entry_time TEXT,
                    exit_time TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    @contextmanager
    def get_connection(self):
        """Context manager для SQLite соединения."""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def get_raw_connection(self) -> sqlite3.Connection:
        """Для случаев когда нужен persistent connection (напр. pandas read_sql)."""
        return sqlite3.connect(self.db_path)
