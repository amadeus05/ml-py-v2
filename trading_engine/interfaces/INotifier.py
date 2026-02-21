from abc import ABC, abstractmethod


class INotifier(ABC):
    """Уведомления — Telegram, Discord, null (для бэктеста)."""

    @abstractmethod
    async def notify(self, message: str, level: str = "info") -> None:
        """
        Отправить уведомление.
        
        Args:
            message: Текст (напр. 'OPEN LONG ETH/USDT at 3000')
            level: 'info', 'warning', 'error'
        """
        ...