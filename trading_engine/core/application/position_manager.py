import logging
from typing import Optional

from trading_engine.core.domain.enums import Side
from trading_engine.core.domain.models import TradeResult
from trading_engine.core.domain.position import Position
from trading_engine.core.domain.portfolio import Portfolio
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Управление позициями: open/close + проверка SL/TP.
    
    Exit логика — 100% как MVP bt.py lines 113-146:
        LONG SL: next_low <= stop_price → exit = min(next_open, stop_price) * (1 - SLIPPAGE)
        LONG TP: next_high >= take_price → exit = take_price * (1 - SLIPPAGE)
        SHORT SL: next_high >= stop_price → exit = max(next_open, stop_price) * (1 + SLIPPAGE)
        SHORT TP: next_low <= take_price → exit = take_price * (1 + SLIPPAGE)
    """

    def __init__(self, portfolio: Portfolio, settings: Settings):
        self.portfolio = portfolio
        self.settings = settings
        self.positions: dict[str, Position] = {}

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def open_position(self, symbol: str, position: Position) -> None:
        """Открыть позицию и залочить маржу."""
        self.positions[symbol] = position
        self.portfolio.lock_margin(position.margin)
        logger.info(
            f"OPEN {position.side.label} {symbol} "
            f"at {position.entry_price:.2f} | "
            f"Size: {position.notional:.1f}$ Margin: {position.margin:.1f}$"
        )

    def close_position(self, symbol: str, exit_price: float, reason: str) -> Optional[TradeResult]:
        """
        Закрыть позицию и обновить баланс.
        
        PnL расчёт через Position.close() — уже включает комиссии.
        """
        if symbol not in self.positions:
            logger.warning(f"No position to close for {symbol}")
            return None

        position = self.positions.pop(symbol)
        
        pnl_result = position.close(
            exit_price=exit_price,
            taker_fee=self.settings.TAKER_FEE,
            slippage=0.0,  # Slippage уже в exit_price
        )

        # Освободить маржу и применить PnL — как MVP bt.py lines 159-163
        self.portfolio.release_margin(position.margin)
        self.portfolio.apply_realized_pnl(pnl_result["pnl_abs"])

        trade_result = TradeResult(
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            notional=position.notional,
            margin=position.margin,
            raw_pnl_pct=pnl_result["raw_pnl_pct"],
            net_pnl_pct=pnl_result["net_pnl_pct"],
            pnl_abs=pnl_result["pnl_abs"],
            commission=pnl_result["commission"],
            reason=reason,
            entry_time=position.entry_time,
        )

        logger.info(
            f"CLOSE {position.side.label} {symbol} | {reason} | "
            f"PnL: {pnl_result['net_pnl_pct'] * 100:.2f}% | "
            f"Com: {pnl_result['commission']:.2f}$ | "
            f"Bal: {self.portfolio.balance:.2f}"
        )

        return trade_result

    def check_exit(
        self,
        symbol: str,
        next_open: float,
        next_high: float,
        next_low: float,
    ) -> Optional[tuple]:
        """
        Проверка SL/TP на следующей свече — 100% как MVP bt.py lines 123-146.
        
        Returns:
            (exit_price, reason) или None
        """
        if symbol not in self.positions:
            return None

        position = self.positions[symbol]
        entry_price = position.entry_price
        slippage = self.settings.SLIPPAGE
        sl_pct = self.settings.SL_PCT
        tp_pct = self.settings.TP_PCT

        if position.side == Side.LONG:
            stop_price = entry_price * (1 - sl_pct)
            take_price = entry_price * (1 + tp_pct)

            # MVP bt.py lines 127-134: SL приоритетнее TP
            if next_low <= stop_price:
                exit_price = (next_open if next_open < stop_price else stop_price) * (1 - slippage)
                return (exit_price, "SL")
            elif next_high >= take_price:
                exit_price = take_price * (1 - slippage)
                return (exit_price, "TP")

        else:  # SHORT
            stop_price = entry_price * (1 + sl_pct)
            take_price = entry_price * (1 - tp_pct)

            # MVP bt.py lines 139-146
            if next_high >= stop_price:
                exit_price = (next_open if next_open > stop_price else stop_price) * (1 + slippage)
                return (exit_price, "SL")
            elif next_low <= take_price:
                exit_price = take_price * (1 + slippage)
                return (exit_price, "TP")

        return None