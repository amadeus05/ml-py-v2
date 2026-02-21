"""
TradingEngine — единый движок, общий для live и backtest.

Вся торговая логика инкапсулирована здесь через компоненты:
    - SignalHandler (ML pipeline)
    - RiskManager (position sizing)
    - PositionManager (open/close + SL/TP check)
    - ExecutionService (order lifecycle)

Внешний код (backtest_runner или live loop) просто вызывает on_candle().
"""
import asyncio
import logging
import time
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
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_reconcile_wall_ts = 0.0
        self._reconcile_lock = asyncio.Lock()

    def _get_lock(self, symbol: str) -> asyncio.Lock:
        lock = self._locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[symbol] = lock
        return lock

    def _to_local_symbol(self, api_symbol: str) -> str:
        """Преобразовать символ биржи без '/' в локальный формат."""
        known_quotes = ("USDT", "BUSD", "USDC", "BTC", "ETH")
        for quote in known_quotes:
            if api_symbol.endswith(quote) and len(api_symbol) > len(quote):
                base = api_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return api_symbol

    async def _reconcile_positions_if_needed(self) -> None:
        if not self.settings.RECONCILE_ENABLED:
            return
        if self.settings.EXCHANGE_MODE != "binance":
            return
        interval = max(1, int(self.settings.RECONCILE_INTERVAL_SEC))
        now_ts = time.time()
        if now_ts - self._last_reconcile_wall_ts < interval:
            return

        async with self._reconcile_lock:
            if now_ts - self._last_reconcile_wall_ts < interval:
                return
            try:
                exchange_positions = await self.execution_service.exchange.get_position_risk()
            except Exception as e:
                logger.warning(f"Reconciliation skipped: failed to fetch position risk: {e}")
                return

            exchange_map: dict[str, dict] = {}
            for p in exchange_positions:
                api_symbol = p.get("symbol")
                if not api_symbol:
                    continue
                local_symbol = self._to_local_symbol(api_symbol)
                exchange_map[local_symbol] = p

            changes = self.position_manager.reconcile_with_exchange(
                exchange_positions=exchange_map,
                qty_tolerance=self.settings.RECONCILE_QTY_TOLERANCE,
            )

            total_changes = sum(len(v) for v in changes.values())
            if total_changes > 0:
                logger.warning(f"Reconciliation applied: {changes}")
                try:
                    await self.execution_service.notifier.notify(
                        f"⚠️ Reconciliation: created={len(changes['created'])}, "
                        f"updated={len(changes['updated'])}, closed={len(changes['closed'])}",
                        level="warning",
                    )
                except Exception as e:
                    logger.warning(f"Failed to send reconciliation notification: {e}")

            self._last_reconcile_wall_ts = now_ts

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
        async with self._get_lock(symbol):
            # === STEP 0: RECONCILE POSITIONS (LIVE SAFETY) ===
            await self._reconcile_positions_if_needed()

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

            # === STEP 1.5: CHECK MAX CONCURRENT POSITIONS ===
            if len(self.position_manager.positions) >= self.settings.MAX_CONCURRENT_POSITIONS:
                # Превышен лимит одновременно открытых позиций
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
