import logging
import aiohttp

from trading_engine.interfaces.INotifier import INotifier

logger = logging.getLogger(__name__)


class TelegramNotifier(INotifier):
    """Telegram уведомления — отправка сообщений через Bot API."""

    def __init__(self, api_key: str, chat_id: str):
        self.api_key = api_key
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{api_key}"
        self._session = None

    async def _get_session(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def notify(self, message: str, level: str = "info") -> None:
        """Отправить сообщение в Telegram."""
        level_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(level, "")
        text = f"{level_emoji} {message}"

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram API returned {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    async def close(self):
        if self._session:
            await self._session.close()
