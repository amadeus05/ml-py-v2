from enum import Enum


class Side(Enum):
    """Направление позиции. value используется как множитель в PnL расчётах."""
    LONG = 1
    SHORT = -1

    @property
    def label(self) -> str:
        return self.name

    @property
    def order_side(self):
        """Конвертация в OrderSide для event bus."""
        from trading_engine.core.domain.events import OrderSide
        return OrderSide.BUY if self == Side.LONG else OrderSide.SELL

    @classmethod
    def from_signal(cls, signal: int) -> "Side":
        """Из числового сигнала (1 = LONG, -1 = SHORT)."""
        if signal == 1:
            return cls.LONG
        elif signal == -1:
            return cls.SHORT
        raise ValueError(f"Invalid signal value: {signal}. Expected 1 or -1.")


class Exchange(Enum):
    BINANCE = "binance"
    BYBIT = "bybit"
    PAPER = "paper"
    SIMULATION = "simulation"  # Backtest


class Timeframe(Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def milliseconds(self) -> int:
        mapping = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        return mapping[self.value]

    @property
    def hours(self) -> float:
        mapping = {
            "1m": 1 / 60,
            "5m": 5 / 60,
            "15m": 0.25,
            "1h": 1,
            "4h": 4,
            "1d": 24,
        }
        return mapping[self.value]