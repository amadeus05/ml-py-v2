import logging
from typing import Optional, Dict
from datetime import datetime

import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings
from trading_engine.adapters.persistence.repositories.candle_repository import SQLiteCandleRepository
from trading_engine.core.domain.enums import Side

logger = logging.getLogger(__name__)


class SimulationExchange(IExchange):
    """
    Backtest exchange — полная локальная симуляция биржи.
    
    Управляет:
        - Чтение OHLCV из SQLite (не делает сетевых запросов)
        - Локальный учёт позиций (open/close)
        - Симуляция fills с slippage
        - Актуальный баланс и маржа
    """

    def __init__(self, settings: Settings, candle_repo: SQLiteCandleRepository):
        self.settings = settings
        self.candle_repo = candle_repo

        self._current_candles: Dict[str, dict] = {}  # symbol → текущая свеча (OHLC)

    def set_current_candle(self, symbol: str, open_: float, high: float, low: float, close: float, timestamp=None) -> None:
        """
        Установить текущую свечу — вызывается на каждом шаге бэктеста.
        Exchange использует эти данные для fill/exit simulation.
        """
        self._current_candles[symbol] = {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "timestamp": timestamp,
        }

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """Загрузка из SQLite (не из API)."""
        df = self.candle_repo.load_candles(symbol, timeframe)
        if since is not None:
            since_dt = pd.to_datetime(since, unit="ms")
            df = df[df["timestamp"] >= since_dt]
        if limit:
            df = df.head(limit)
        return df

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
        reduce_only: bool = False,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> dict:
        """
        Симуляция fill.
        
        Slippage как в MVP bt.py lines 250/254:
            LONG:  entry = next_open * (1 + SLIPPAGE)
            SHORT: entry = next_open * (1 - SLIPPAGE)
        """
        slippage = self.settings.SLIPPAGE

        if side == "BUY":
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        notional = quantity * fill_price
        margin = notional / self.settings.LEVERAGE

        logger.debug(
            f"[SIM] FILL {side} {symbol} qty={quantity:.6f} "
            f"fill={fill_price:.2f} margin={margin:.2f}"
        )

        return {
            "fill_price": fill_price,
            "fill_quantity": quantity,
            "commission": 0.0,  # Считается в Position.close()
            "exchange_order_id": f"SIM-{symbol}-{side}",
        }



    async def get_balance(self) -> float:
        """
        Для симуляции баланс отслеживается в Portfolio.
        Здесь возвращаем 0.0 (или можно кидать Exception, т.к. не должно вызываться напряму).
        """
        return 0.0

    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """Для симуляции нет биржи — возвращаем пустой список."""
        return []

    async def close(self) -> None:
        pass
