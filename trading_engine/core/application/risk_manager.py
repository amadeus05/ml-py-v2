import logging
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

    def validate(self, signal) -> Optional[dict]:
        """
        Валидация сигнала и расчёт позиции.
        
        Args:
            signal: Signal object (side, confidence, predicted_mfe)
        
        Returns:
            dict с position params (notional, margin, quantity) или None если rejected
        """
        result = self.risk_engine.calculate_position(
            balance=self.portfolio.balance,
            price=0.0,  # Price будет при entry (next_open)
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