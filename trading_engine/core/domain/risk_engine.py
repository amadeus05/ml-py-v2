import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Чистый доменный расчёт размера позиции.
    
    100% калька с MVP bt.py lines 231-245:
        risk_capital = balance * RISK_PER_TRADE
        position_notional = risk_capital / SL_PCT
        max_notional = balance * LEVERAGE
        position_notional = min(position_notional, max_notional)
        required_margin = position_notional / LEVERAGE
        available_balance = balance - used_margin
        if required_margin > available_balance: clamp
        if position_notional < min_notional: reject
    """

    def calculate_position(
        self,
        balance: float,
        price: float,
        sl_pct: float,
        risk_per_trade: float,
        leverage: float,
        used_margin: float,
        min_notional: float = 10.0,
    ) -> Optional[dict]:
        """
        Рассчитывает размер позиции с учётом риска и маржи.
        
        Args:
            balance: Текущий баланс
            price: Текущая цена актива
            sl_pct: Stop-loss в процентах (напр. 0.015 = 1.5%)
            risk_per_trade: Доля баланса под риск (напр. 0.01 = 1%)
            leverage: Кредитное плечо
            used_margin: Уже заблокированная маржа
            min_notional: Минимальный размер позиции ($)
        
        Returns:
            dict с ключами: notional, margin, quantity
            None если позицию открыть невозможно
        """
        if balance <= 0 or price <= 0 or sl_pct <= 0:
            logger.warning(f"Invalid inputs: balance={balance}, price={price}, sl_pct={sl_pct}")
            return None

        # MVP bt.py line 231: risk_capital = balance * RISK_PER_TRADE
        risk_capital = balance * risk_per_trade

        # MVP bt.py line 232: position_notional = risk_capital / SL_PCT
        position_notional = risk_capital / sl_pct

        # MVP bt.py line 234: max_notional = balance * LEVERAGE
        max_notional = balance * leverage

        # MVP bt.py line 235: position_notional = min(position_notional, max_notional)
        position_notional = min(position_notional, max_notional)

        # MVP bt.py line 237: required_margin = position_notional / LEVERAGE
        required_margin = position_notional / leverage

        # MVP bt.py lines 238-242: clamp to available balance
        available_balance = balance - used_margin
        if required_margin > available_balance:
            required_margin = available_balance
            position_notional = required_margin * leverage

        # MVP bt.py line 244: if position_notional < 10: skip
        if position_notional < min_notional:
            logger.debug(f"Position too small: {position_notional:.2f} < {min_notional}")
            return None

        quantity = position_notional / price

        return {
            "notional": position_notional,
            "margin": required_margin,
            "quantity": quantity,
        }