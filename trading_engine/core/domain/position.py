from datetime import datetime
from typing import Optional

from trading_engine.core.domain.enums import Side


class Position:
    """
    Открытая позиция. PnL расчёт идентичен MVP bt.py (lines 149-156):
    
        raw_pnl = (exit - entry) / entry  [LONG]
        raw_pnl = (entry - exit) / entry  [SHORT]
        net_pnl = raw_pnl - (taker_fee * 2)   # open + close
        trade_profit = notional * net_pnl
    """

    def __init__(
        self,
        symbol: str,
        entry_price: float,
        side: Side,
        notional: float,
        margin: float,
        leverage: float,
        entry_time: Optional[datetime] = None,
        quantity: Optional[float] = None,
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.side = side
        self.leverage = leverage
        self.entry_time = entry_time or datetime.utcnow()
        if quantity is not None:
            self.quantity = quantity
            self.notional = quantity * entry_price
            self.margin = self.notional / leverage
        else:
            self.notional = notional
            self.margin = margin
            self.quantity = notional / entry_price

    def close(self, exit_price: float, taker_fee: float, slippage: float) -> dict:
        """
        Закрытие позиции с расчётом PnL.
        
        100% повторяет MVP bt.py lines 149-156:
            raw_pnl = (exit_price - entry_price) / entry_price   # LONG
            commission = notional * (taker_fee + taker_fee)
            pnl_clean = raw_pnl - (taker_fee + taker_fee)
            trade_profit = notional * pnl_clean
        
        Returns:
            dict с ключами: raw_pnl_pct, net_pnl_pct, pnl_abs, commission
        """
        if self.side == Side.LONG:
            raw_pnl = (exit_price - self.entry_price) / self.entry_price
        else:
            raw_pnl = (self.entry_price - exit_price) / self.entry_price

        # Комиссии: taker на вход + taker на выход (как в MVP)
        total_fee_pct = taker_fee + taker_fee
        commission = self.notional * total_fee_pct
        net_pnl_pct = raw_pnl - total_fee_pct
        trade_profit = self.notional * net_pnl_pct

        return {
            "raw_pnl_pct": raw_pnl,
            "net_pnl_pct": net_pnl_pct,
            "pnl_abs": trade_profit,
            "commission": commission,
        }

    def get_stop_price(self, sl_pct: float) -> float:
        """SL уровень — зеркальный для LONG/SHORT."""
        if self.side == Side.LONG:
            return self.entry_price * (1 - sl_pct)
        else:
            return self.entry_price * (1 + sl_pct)

    def get_take_price(self, tp_pct: float) -> float:
        """TP уровень — зеркальный для LONG/SHORT."""
        if self.side == Side.LONG:
            return self.entry_price * (1 + tp_pct)
        else:
            return self.entry_price * (1 - tp_pct)

    def __repr__(self) -> str:
        return (
            f"Position({self.symbol} {self.side.label} "
            f"entry={self.entry_price:.2f} notional={self.notional:.2f} "
            f"margin={self.margin:.2f})"
        )