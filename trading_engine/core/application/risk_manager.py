import logging
from datetime import datetime, date
from typing import Optional

from trading_engine.core.domain.risk_engine import RiskEngine
from trading_engine.core.domain.portfolio import Portfolio
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Валидация сигнала и расчёт размера позиции.
    
    Все параметры берутся из Settings (не hardcoded).
    Логика — делегирование в RiskEngine (который = MVP bt.py lines 231-245).
    """

    def __init__(self, portfolio: Portfolio, risk_engine: RiskEngine, settings: Settings):
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.settings = settings
        self._daily_start_balance: Optional[float] = None
        self._daily_start_date: Optional[date] = None

    def _refresh_daily_start(self) -> None:
        today = datetime.utcnow().date()
        if self._daily_start_date != today or self._daily_start_balance is None:
            self._daily_start_date = today
            self._daily_start_balance = self.portfolio.balance

    def _daily_loss_pct(self) -> Optional[float]:
        self._refresh_daily_start()
        if self._daily_start_balance is None or self._daily_start_balance <= 0:
            return None
        loss = (self._daily_start_balance - self.portfolio.balance) / self._daily_start_balance
        return max(0.0, loss * 100)

    def _check_kill_switch(self) -> Optional[str]:
        max_daily_loss = float(getattr(self.settings, "MAX_DAILY_LOSS_PCT", 0.0) or 0.0)
        if max_daily_loss > 0:
            daily_loss = self._daily_loss_pct()
            if daily_loss is not None and daily_loss >= max_daily_loss:
                return (
                    f"Daily loss limit reached: {daily_loss:.2f}% >= "
                    f"{max_daily_loss:.2f}%"
                )

        max_drawdown = float(getattr(self.settings, "MAX_DRAWDOWN_PCT", 0.0) or 0.0)
        if max_drawdown > 0 and self.portfolio.max_drawdown >= max_drawdown:
            return (
                f"Max drawdown limit reached: {self.portfolio.max_drawdown:.2f}% >= "
                f"{max_drawdown:.2f}%"
            )

        return None

    def validate(self, signal, price: Optional[float] = None) -> Optional[dict]:
        """
        Валидация сигнала и расчёт позиции.
        
        Args:
            signal: Signal object (side, confidence, predicted_mfe)
        
        Returns:
            dict с position params (notional, margin, quantity) или None если rejected
        """
        kill_reason = self._check_kill_switch()
        if kill_reason:
            logger.warning(f"Risk kill switch: {kill_reason}")
            return None

        if price is None or price <= 0:
            logger.warning("Risk validation skipped: entry price is not provided.")
            return None

        result = self.risk_engine.calculate_position(
            balance=self.portfolio.balance,
            price=price,
            sl_pct=self.settings.SL_PCT,
            risk_per_trade=self.settings.RISK_PER_TRADE,
            leverage=self.settings.LEVERAGE,
            used_margin=self.portfolio.used_margin,
            min_notional=self.settings.MIN_NOTIONAL,
        )

        if result is None:
            logger.debug(f"Risk rejected signal: insufficient margin or position too small")
            return None

        return result

    def calculate_entry(self, price: float) -> Optional[dict]:
        """
        Расчёт позиции с конкретной ценой входа.
        
        100% как MVP bt.py lines 231-245.
        """
        kill_reason = self._check_kill_switch()
        if kill_reason:
            logger.warning(f"Risk kill switch: {kill_reason}")
            return None

        result = self.risk_engine.calculate_position(
            balance=self.portfolio.balance,
            price=price,
            sl_pct=self.settings.SL_PCT,
            risk_per_trade=self.settings.RISK_PER_TRADE,
            leverage=self.settings.LEVERAGE,
            used_margin=self.portfolio.used_margin,
            min_notional=self.settings.MIN_NOTIONAL,
        )

        if result is None:
            logger.debug(f"Risk rejected: insufficient margin or position too small")
            return None

        return result