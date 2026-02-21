import logging
from typing import Optional

import pandas as pd

from trading_engine.interfaces.IRepository import ITradeRepository
from trading_engine.adapters.persistence.database import DatabaseManager

logger = logging.getLogger(__name__)


class SQLiteTradeRepository(ITradeRepository):
    """Логирование сделок в SQLite."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def save_trade(self, trade: dict) -> None:
        """Сохранить результат сделки."""
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO trades 
                   (symbol, side, entry_price, exit_price, quantity, notional, margin,
                    raw_pnl_pct, net_pnl_pct, pnl_abs, commission, reason, entry_time, exit_time)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade["symbol"],
                    trade["side"],
                    trade["entry_price"],
                    trade["exit_price"],
                    trade["quantity"],
                    trade["notional"],
                    trade["margin"],
                    trade["raw_pnl_pct"],
                    trade["net_pnl_pct"],
                    trade["pnl_abs"],
                    trade["commission"],
                    trade["reason"],
                    str(trade.get("entry_time", "")),
                    str(trade.get("exit_time", "")),
                ),
            )
            conn.commit()
        logger.debug(f"Trade saved: {trade['symbol']} {trade['side']} PnL={trade['net_pnl_pct']:.4f}")

    def load_trades(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Загрузить историю сделок."""
        conn = self.db.get_raw_connection()
        try:
            if symbol:
                df = pd.read_sql(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY id", conn, params=(symbol,)
                )
            else:
                df = pd.read_sql("SELECT * FROM trades ORDER BY id", conn)
            return df
        finally:
            conn.close()
