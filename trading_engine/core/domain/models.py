from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


from trading_engine.core.domain.enums import Side

@dataclass
class Candle:
    """OHLCV свеча — основной носитель рыночных данных."""
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0


@dataclass
class Signal:
    """Результат ML-пайплайна: направление, уверенность, предсказанный MFE."""
    symbol: str
    side: Side
    confidence: float
    predicted_mfe: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TradeResult:
    """Результат завершённой сделки — для логирования и отчётов."""
    symbol: str
    side: Side              # Enum: Side.LONG or Side.SHORT
    entry_price: float
    exit_price: float
    quantity: float
    notional: float
    margin: float
    raw_pnl_pct: float      # PnL без комиссий (%)
    net_pnl_pct: float      # PnL с комиссиями (%)
    pnl_abs: float           # Абсолютный PnL ($)
    commission: float        # Общая комиссия ($)
    reason: str              # "TP", "SL", "SIGNAL"
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None