import logging
from datetime import datetime
from typing import Optional

from trading_engine.core.domain.enums import Side
from trading_engine.core.domain.position import Position
from trading_engine.core.domain.models import Signal, TradeResult
from trading_engine.core.application.risk_manager import RiskManager
from trading_engine.core.application.position_manager import PositionManager
from trading_engine.interfaces.IExchange import IExchange
from trading_engine.interfaces.INotifier import INotifier
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class ExecutionService:
    """
    Order lifecycle — координирует entry/exit через exchange + position_manager.
    """

    def __init__(
        self,
        exchange: IExchange,
        position_manager: PositionManager,
        risk_manager: RiskManager,
        notifier: INotifier,
        settings: Settings,
    ):
        self.exchange = exchange
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.notifier = notifier
        self.settings = settings

    async def execute_entry(
        self,
        signal: Signal,
        entry_price: float,
        entry_time: Optional[datetime] = None,
    ) -> Optional[Position]:
        """
        Исполнение входа:
        1. RiskManager → position sizing
        2. Exchange → fill (или симуляция)
        3. PositionManager → open_position
        
        Entry price + slippage — как MVP bt.py lines 249-254.
        """
        # 1. Риск-расчёт
        sizing = self.risk_manager.calculate_entry(entry_price)
        if sizing is None:
            return None

        # 2. Place order через exchange
        side_str = "BUY" if signal.side == Side.LONG else "SELL"
        fill = await self.exchange.place_order(
            symbol=signal.symbol,
            side=side_str,
            quantity=sizing["quantity"],
            price=entry_price,
        )

        fill_price = fill["fill_price"]
        fill_qty = float(fill.get("fill_quantity", sizing["quantity"]))
        if fill_qty <= 0:
            fill_qty = sizing["quantity"]

        real_notional = fill_qty * fill_price
        real_margin = real_notional / self.settings.LEVERAGE

        # 3. Создаём Position
        position = Position(
            symbol=signal.symbol,
            entry_price=fill_price,
            side=signal.side,
            notional=real_notional,
            margin=real_margin,
            leverage=self.settings.LEVERAGE,
            entry_time=entry_time,
            quantity=fill_qty,
        )

        # 4. Открываем позицию
        self.position_manager.open_position(signal.symbol, position)

        # 5. Уведомление
        await self.notifier.notify(
            f"🚀 OPEN {signal.side.label} {signal.symbol} "
            f"at {fill_price:.2f} | "
            f"Sig: {signal.confidence:.2f} MFE: {signal.predicted_mfe * 100:.1f}% | "
            f"Size: {position.notional:.1f}$ Margin: {position.margin:.1f}$"
        )

        return position

    async def execute_exit(
        self,
        symbol: str,
        exit_price: float,
        reason: str,
        exit_time: Optional[datetime] = None,
    ) -> Optional[TradeResult]:
        """
        Исполнение выхода:
        1. Если есть позиция, кидаем встречный MARKET ордер на биржу.
        2. PositionManager → close_position с реальным fill_price из ответа биржи.
        3. Notifier → сообщение.
        """
        if not self.position_manager.has_position(symbol):
            return None

        position = self.position_manager.positions[symbol]
        
        # Размещаем встречный ордер на бирже (закрытие)
        side_str = "SELL" if position.side == Side.LONG else "BUY"
        try:
            fill = await self.exchange.place_order(
                symbol=symbol,
                side=side_str,
                quantity=position.quantity,
                price=exit_price,  # Для MARKET используется как запасной/ориентировочный
                reduce_only=True,
            )
            actual_exit_price = fill["fill_price"]
        except Exception as e:
            logger.error(f"Failed to execute exit order on exchange for {symbol}: {e}")
            # Возврат (или можно сделать retry, но пока fallback к расчетной цене)
            actual_exit_price = exit_price

        trade_result = self.position_manager.close_position(symbol, actual_exit_price, reason)
        if trade_result is None:
            return None

        trade_result.exit_time = exit_time

        reason_emoji = "✅ TP" if reason == "TP" else "❌ SL" if reason == "SL" else reason
        await self.notifier.notify(
            f"{reason_emoji} CLOSE {trade_result.side} {symbol} | "
            f"PnL: {trade_result.net_pnl_pct * 100:.2f}% | "
            f"Com: {trade_result.commission:.2f}$ | "
            f"Bal: {self.position_manager.portfolio.balance:.2f}"
        )

        return trade_result
