"""
Trading Engine — Entry Point.

Usage:
    python main.py                    # Default: backtest mode
    python main.py --mode backtest    # Backtest
    python main.py --mode paper       # Paper trading
    python main.py --mode live        # Live trading (Binance)
    python main.py --mode etl         # ETL pipeline (data load + features)
    python main.py --mode train       # Train ML models
"""
import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Trading Engine")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live", "etl", "train"],
        default="backtest",
        help="Execution mode (default: backtest)",
    )
    args = parser.parse_args()

    if args.mode == "backtest":
        from trading_engine.backtest.backtest_runner import run_backtest
        asyncio.run(run_backtest())

    elif args.mode == "etl":
        from trading_engine.scripts.etl_runner import run_etl
        asyncio.run(run_etl())

    elif args.mode == "train":
        from trading_engine.scripts.train_runner import run_training
        run_training()

    elif args.mode in ("paper", "live"):
        from trading_engine.adapters.config.settings import Settings
        from trading_engine.bootstrap.container import Container
        from trading_engine.live.live_runner import LiveRunner

        settings = Settings(
            EXCHANGE_MODE="binance" if args.mode == "live" else "paper",
            TG_ENABLED=True
        )

        container = Container(settings)
        container.build()
        container.load_models()

        logger.info(f"🚀 Starting {args.mode} trading mode...")
        logger.info(f"   Symbols: {settings.SYMBOLS}")
        logger.info(f"   Leverage: {settings.LEVERAGE}x")
        logger.info(f"   Risk/trade: {settings.RISK_PER_TRADE * 100:.1f}%")

        runner = LiveRunner(container, settings)
        try:
            asyncio.run(runner.run())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()