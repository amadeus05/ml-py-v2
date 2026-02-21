import logging
from datetime import datetime
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
                exit_price = next_open if next_open < stop_price else stop_price
                exit_price *= (1 - slippage)
                return (exit_price, "SL")
            elif next_high >= take_price:
                exit_price = take_price
                return (exit_price, "TP")

        else:  # SHORT
            stop_price = entry_price * (1 + sl_pct)
            take_price = entry_price * (1 - tp_pct)

            # MVP bt.py lines 139-146
            if next_high >= stop_price:
                exit_price = next_open if next_open > stop_price else stop_price
                exit_price *= (1 + slippage)
                return (exit_price, "SL")
            elif next_low <= take_price:
                exit_price = take_price
                return (exit_price, "TP")

        return None

    def reconcile_with_exchange(
        self,
        exchange_positions: dict[str, dict],
        qty_tolerance: float,
    ) -> dict:
        """
        Аварийная синхронизация локальных позиций с биржей.

        exchange_positions: dict с ключами local symbol (например "BTC/USDT")
            и значениями:
                {
                    "quantity": signed_qty,
                    "entry_price": float,
                    "leverage": int,
                    "mark_price": float,
                }
        """
        changes = {"created": [], "updated": [], "closed": []}
        now = datetime.utcnow()

        # 1) Закрыть/обновить существующие локальные позиции
        for symbol, position in list(self.positions.items()):
            exch = exchange_positions.get(symbol)
            if exch is None or abs(exch.get("quantity", 0.0)) <= qty_tolerance:
                self.positions.pop(symbol, None)
                changes["closed"].append(symbol)
                continue

            exch_qty = float(exch.get("quantity", 0.0))
            exch_side = Side.LONG if exch_qty > 0 else Side.SHORT
            exch_qty_abs = abs(exch_qty)
            local_signed_qty = (
                position.quantity if position.side == Side.LONG else -position.quantity
            )

            entry_price = float(exch.get("entry_price") or 0.0)
            if entry_price <= 0:
                entry_price = float(exch.get("mark_price") or position.entry_price)

            leverage = int(exch.get("leverage") or position.leverage)

            if (
                abs(exch_qty - local_signed_qty) > qty_tolerance
                or exch_side != position.side
                or abs(entry_price - position.entry_price) > 0
            ):
                position.side = exch_side
                position.entry_price = entry_price
                position.leverage = leverage
                position.quantity = exch_qty_abs
                position.notional = exch_qty_abs * entry_price
                position.margin = position.notional / leverage
                position.entry_time = position.entry_time or now
                changes["updated"].append(symbol)

        # 2) Открыть позиции, которые есть на бирже, но нет локально
        for symbol, exch in exchange_positions.items():
            if symbol in self.positions:
                continue
            exch_qty = float(exch.get("quantity", 0.0))
            if abs(exch_qty) <= qty_tolerance:
                continue

            entry_price = float(exch.get("entry_price") or 0.0)
            if entry_price <= 0:
                entry_price = float(exch.get("mark_price") or 0.0)
            leverage = int(exch.get("leverage") or self.settings.LEVERAGE)

            side = Side.LONG if exch_qty > 0 else Side.SHORT
            qty_abs = abs(exch_qty)
            notional = qty_abs * entry_price
            margin = notional / leverage if leverage > 0 else 0.0

            self.positions[symbol] = Position(
                symbol=symbol,
                entry_price=entry_price,
                side=side,
                notional=notional,
                margin=margin,
                leverage=leverage,
                entry_time=now,
                quantity=qty_abs,
            )
            changes["created"].append(symbol)

        # 3) Синхронизировать used_margin
        total_margin = sum(p.margin for p in self.positions.values())
        self.portfolio.sync_used_margin(total_margin)

        return changes