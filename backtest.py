import asyncio
import logging
from trading_engine.backtest.backtest_runner import run_backtest

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_backtest())
