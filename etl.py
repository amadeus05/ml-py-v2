import asyncio
import logging
from trading_engine.scripts.etl_runner import run_etl

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_etl())
