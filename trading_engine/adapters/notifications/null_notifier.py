import logging

from trading_engine.interfaces.INotifier import INotifier

logger = logging.getLogger(__name__)


class NullNotifier(INotifier):
    """No-op notifier — для бэктеста (TG отключен)."""

    async def notify(self, message: str, level: str = "info") -> None:
        """Просто логируем, не отправляем."""
        logger.debug(f"[NULL_NOTIFIER] {message}")
