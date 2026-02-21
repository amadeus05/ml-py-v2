import logging
import asyncio
import html
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
        """Отправить сообщение в Telegram с retry policy."""
        level_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(level, "")
        safe_message = html.escape(message)
        text = f"{level_emoji} {safe_message}".strip()
        if len(text) > 4096:
            text = text[:4093] + "..."
        
        max_retries = 3
        base_backoff = 1.0

        for attempt in range(max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (429, 500, 502, 503, 504) and attempt < max_retries:
                        backoff = base_backoff * (2 ** attempt)
                        if resp.status == 429:
                            try:
                                data = await resp.json()
                                retry_after = data.get("parameters", {}).get("retry_after")
                                if retry_after:
                                    backoff = max(backoff, float(retry_after))
                            except Exception:
                                pass
                        logger.warning(
                            f"Telegram API {resp.status}. Retrying in {backoff}s "
                            f"(Attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(backoff)
                        continue

                    if resp.status != 200:
                        try:
                            data = await resp.json()
                            logger.warning(f"Telegram API error {resp.status}: {data}")
                        except Exception:
                            logger.warning(f"Telegram API returned {resp.status}")
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries:
                    backoff = base_backoff * (2 ** attempt)
                    logger.warning(f"Telegram API error: {e}. Retrying in {backoff}s (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(backoff)
                    continue
                logger.error(f"Failed to send Telegram notification after {max_retries} attempts: {e}")
                return
            except Exception as e:
                logger.error(f"Failed to send Telegram notification: {e}")
                return

    async def close(self):
        if self._session:
            await self._session.close()
