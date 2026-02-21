"""
TradingEngine — единый движок, общий для live и backtest.

Вся торговая логика инкапсулирована здесь через компоненты:
    - SignalHandler (ML pipeline)
    - RiskManager (position sizing)
    - PositionManager (open/close + SL/TP check)
    - ExecutionService (order lifecycle)

Внешний код (backtest_runner или live loop) просто вызывает on_candle().
"""
import logging
from datetime import datetime
from typing import Optional, List

from trading_engine.core.domain.models import TradeResult
from trading_engine.core.application.signal_handler import SignalHandler
from trading_engine.core.application.risk_manager import RiskManager
from trading_engine.core.application.position_manager import PositionManager
from trading_engine.core.application.execution_service import ExecutionService
from trading_engine.core.domain.portfolio import Portfolio
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Ядро бота — одинаковый flow для live и backtest.
    
    on_candle() — основной метод. Вызывается на каждой новой свече.
    Внутри:
        1. Check exits (SL/TP) через PositionManager
        2. Generate signal через SignalHandler
        3. Execute entry через ExecutionService
    """

    def __init__(
        self,
        signal_handler: SignalHandler,
        position_manager: PositionManager,
        execution_service: ExecutionService,
        portfolio: Portfolio,
        settings: Settings,
    ):
        self.signal_handler = signal_handler
        self.position_manager = position_manager
        self.execution_service = execution_service
        self.portfolio = portfolio
        self.settings = settings
        self.trade_results: List[TradeResult] = []

    async def on_candle(
        self,
        symbol: str,
        df,
        row_index: int,
        next_open: float,
        next_high: float,
        next_low: float,
        next_ts: datetime,
    ) -> Optional[TradeResult]:
        """
        Обработка одной свечи — ПОЛНЫЙ цикл бота.
        
        Это ЕДИНСТВЕННЫЙ метод, который нужно вызывать.
        Live бот и backtest вызывают один и тот же on_candle().
        
        Args:
            symbol: Торговая пара
            df: DataFrame с фичами (для ML predict)
            row_index: Индекс текущей строки в df
            next_open/high/low: OHLC следующей свечи (для exit check + entry)
            next_ts: Timestamp следующей свечи
        
        Returns:
            TradeResult если была закрыта позиция, иначе None
        """
        # === STEP 1: CHECK EXIT (SL/TP) ===
        if self.position_manager.has_position(symbol):
            exit_result = self.position_manager.check_exit(
                symbol, next_open, next_high, next_low
            )
            if exit_result:
                exit_price, reason = exit_result
                trade_result = await self.execution_service.execute_exit(
                    symbol, exit_price, reason, exit_time=next_ts
                )
                if trade_result:
                    self.trade_results.append(trade_result)
                    return trade_result
            # Если позиция открыта но не вышли — не входим повторно
            return None

        # === STEP 2: GENERATE SIGNAL (ML) ===
        signal = self.signal_handler.process(
            df=df,
            symbol=symbol,
            row_index=row_index,
        )

        if signal is None:
            return None

        signal.symbol = symbol

        # === STEP 3: EXECUTE ENTRY ===
        await self.execution_service.execute_entry(
            signal=signal,
            entry_price=next_open,
            entry_time=next_ts,
        )

        return None
