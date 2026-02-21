import asyncio
import logging
from trading_engine.adapters.config.settings import Settings
from trading_engine.bootstrap.container import Container
from trading_engine.live.live_runner import LiveRunner

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    # По умолчанию запускаем live режим (Binance) с включенным Telegram
    settings = Settings(EXCHANGE_MODE="binance", TG_ENABLED=True)
    container = Container(settings)
    container.build()
    container.load_models()

    runner = LiveRunner(container, settings)
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
