import logging
from typing import Optional

import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class PaperExchange(IExchange):
    """
    Paper trading exchange — использует реальные данные Binance API
    для fetch_ohlcv, но ордера исполняет локально (симуляция fill).
    
    Slippage применяется при fill — как в MVP bt.py.
    """

    def __init__(self, settings: Settings, data_exchange: IExchange, initial_balance: float = 100.0):
        """
        Args:
            settings: Config settings
            data_exchange: Реальная биржа для получения данных (BinanceExchange)
            initial_balance: Стартовый баланс
        """
        self.settings = settings
        self.data_exchange = data_exchange
        self._balance = initial_balance
        self._last_prices: dict[str, float] = {}
        self._positions: dict[str, dict] = {}
        self._conditional_orders: dict[str, list[dict]] = {}

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """Использует реальную биржу для данных."""
        df = await self.data_exchange.fetch_ohlcv(symbol, timeframe, since, limit)
        if not df.empty and "close" in df.columns:
            self._last_prices[symbol] = df["close"].iloc[-1]
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
        Симуляция ордера: fill по текущей цене + slippage.
        Slippage как в MVP bt.py lines 250/254.
        """
        slippage = self.settings.SLIPPAGE
        order_type_upper = order_type.upper()
        is_trigger_order = order_type_upper in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
        trigger_price = stop_price if stop_price is not None else price

        if is_trigger_order:
            order = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type_upper,
                "trigger_price": trigger_price,
                "reduce_only": reduce_only,
            }
            self._conditional_orders.setdefault(symbol, []).append(order)
            logger.info(
                f"[PAPER] QUEUE {order_type_upper} {symbol} side={side} "
                f"qty={quantity:.6f} trigger={trigger_price:.2f}"
            )
            return {
                "fill_price": 0.0,
                "fill_quantity": 0.0,
                "commission": 0.0,
                "exchange_order_id": f"PAPER-{symbol}-{order_type_upper}",
            }
        
        if side == "BUY":
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        commission = quantity * fill_price * self.settings.TAKER_FEE

        logger.info(
            f"[PAPER] {side} {symbol} qty={quantity:.6f} "
            f"price={price:.2f} → fill={fill_price:.2f} comm={commission:.4f}"
        )

        if reduce_only:
            self._close_position(symbol)
        else:
            self._open_position(symbol, side, quantity, fill_price)

        return {
            "fill_price": fill_price,
            "fill_quantity": quantity,
            "commission": commission,
            "exchange_order_id": f"PAPER-{symbol}-{side}",
        }

    async def get_balance(self) -> float:
        return self._balance

    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """Позиции для reconciliation в paper-режиме."""
        results = []
        symbols_set = {s.replace("/", "") for s in symbols} if symbols else None

        for symbol, pos in self._positions.items():
            api_symbol = symbol.replace("/", "")
            if symbols_set and api_symbol not in symbols_set:
                continue
            results.append({
                "symbol": api_symbol,
                "quantity": pos["quantity"],
                "entry_price": pos["entry_price"],
                "leverage": pos["leverage"],
                "mark_price": pos.get("mark_price", pos["entry_price"]),
            })

        return results

    def set_balance(self, balance: float) -> None:
        """Установить начальный баланс для paper trading."""
        self._balance = balance

    def process_candle(
        self, symbol: str, open_: float, high: float, low: float, close: float
    ) -> None:
        """Обрабатывает свечу и триггерит SL/TP ордера."""
        self._last_prices[symbol] = close
        if symbol in self._positions:
            self._positions[symbol]["mark_price"] = close

        orders = self._conditional_orders.get(symbol, [])
        if not orders:
            return

        # SL приоритетнее TP
        orders = sorted(orders, key=lambda o: 0 if o["order_type"] == "STOP_MARKET" else 1)
        triggered = None
        for order in orders:
            if self._is_triggered(order, high=high, low=low):
                triggered = order
                break

        if not triggered:
            return

        fill_price = self._apply_slippage(triggered["trigger_price"], triggered["side"])
        self._close_position(symbol)
        self._conditional_orders.pop(symbol, None)
        logger.info(
            f"[PAPER] TRIGGER {triggered['order_type']} {symbol} "
            f"side={triggered['side']} fill={fill_price:.2f}"
        )

    def _is_triggered(self, order: dict, high: float, low: float) -> bool:
        trigger_price = order["trigger_price"]
        order_type = order["order_type"]
        side = order["side"]

        if order_type == "STOP_MARKET":
            if side == "SELL":
                return low <= trigger_price
            return high >= trigger_price

        if order_type == "TAKE_PROFIT_MARKET":
            if side == "SELL":
                return high >= trigger_price
            return low <= trigger_price

        return False

    def _apply_slippage(self, price: float, side: str) -> float:
        slippage = self.settings.SLIPPAGE
        return price * (1 + slippage) if side == "BUY" else price * (1 - slippage)

    def _open_position(self, symbol: str, side: str, quantity: float, entry_price: float) -> None:
        signed_qty = quantity if side == "BUY" else -quantity
        self._positions[symbol] = {
            "quantity": signed_qty,
            "entry_price": entry_price,
            "leverage": self.settings.LEVERAGE,
            "mark_price": entry_price,
        }
        self._conditional_orders.pop(symbol, None)

    def _close_position(self, symbol: str) -> None:
        self._positions.pop(symbol, None)
        self._conditional_orders.pop(symbol, None)

    async def close(self) -> None:
        await self.data_exchange.close()