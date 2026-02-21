import logging

logger = logging.getLogger(__name__)


class Portfolio:
    """
    Управление балансом и маржой.
    
    Логика идентична MVP bt.py:
        - balance = текущий свободный баланс
        - used_margin = заблокированная маржа по открытым позициям
        - available = balance - used_margin
        - При закрытии: balance += trade_profit, used_margin -= margin
    """

    def __init__(self, initial_balance: float):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.used_margin = 0.0
        self.realized_pnl = 0.0
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0

    @property
    def available_margin(self) -> float:
        return self.balance - self.used_margin

    @property
    def equity(self) -> float:
        """Equity = balance (unrealized PnL не считаем в этой версии)."""
        return self.balance

    def lock_margin(self, amount: float) -> None:
        """Блокировка маржи при открытии позиции."""
        self.used_margin += amount
        logger.debug(f"Margin locked: {amount:.2f}, used_margin: {self.used_margin:.2f}")

    def release_margin(self, amount: float) -> None:
        """Освобождение маржи при закрытии позиции — как в MVP bt.py lines 159-161."""
        self.used_margin -= amount
        if self.used_margin < 0:
            self.used_margin = 0.0
        logger.debug(f"Margin released: {amount:.2f}, used_margin: {self.used_margin:.2f}")

    def sync_used_margin(self, amount: float) -> None:
        """
        Принудительная синхронизация used_margin (reconciliation).
        Используется при расхождении локальной позиции с биржей.
        """
        if amount < 0:
            amount = 0.0
        self.used_margin = amount
        logger.warning(f"Margin synced to: {self.used_margin:.2f}")

    def apply_realized_pnl(self, pnl: float) -> None:
        """Применение реализованного PnL — как в MVP bt.py line 163."""
        self.realized_pnl += pnl
        self.balance += pnl

        # Обновление max drawdown — как в MVP bt.py lines 171-175
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd

    def __repr__(self) -> str:
        return (
            f"Portfolio(balance={self.balance:.2f}, "
            f"used_margin={self.used_margin:.2f}, "
            f"available={self.available_margin:.2f}, "
            f"dd={self.max_drawdown:.2f}%)"
        )