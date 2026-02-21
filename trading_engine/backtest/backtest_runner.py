"""
Backtest Runner — тонкий оркестратор.

Вся торговая логика — в TradingEngine (тот же что и live бот).
Вся отчётность — в BacktestReporter.
Этот файл только: загрузка данных → feed candles → print report.
"""
import asyncio
import logging

from trading_engine.adapters.config.settings import Settings
from trading_engine.bootstrap.container import Container
from trading_engine.backtest.backtest_reporter import BacktestReporter


logger = logging.getLogger(__name__)


async def run_backtest(settings: Settings = None):
    if settings is None:
        settings = Settings()
    settings.EXCHANGE_MODE = "simulation"
    settings.TG_ENABLED = False

    # --- 1. BUILD (те же компоненты что и live бот) ---
    container = Container(settings)
    container.build(initial_balance=100.0)
    container.load_models()

    feature_names = container.model.get_feature_names()

    # --- 2. LOAD DATA ---
    all_dfs = {}
    for sym in settings.SYMBOLS:
        try:
            df = container.candle_repo.load_features(sym, feature_names)
            if "timestamp" in df.columns:
                all_dfs[sym] = df
        except Exception as e:
            logger.warning(f"⚠️ Error loading {sym}: {e}")

    if not all_dfs:
        print("Error: No data for backtest!")
        return

    # --- 3. SPLIT TEST PERIOD (last 15%) ---
    common_ts = sorted(set.intersection(*(set(df["timestamp"]) for df in all_dfs.values())))
    split_idx = int(len(common_ts) * 0.85)
    test_ts = common_ts[split_idx:]
    if not test_ts:
        print("Error: Not enough data!")
        return

    for sym in all_dfs:
        df = all_dfs[sym]
        all_dfs[sym] = df[df["timestamp"].isin(test_ts)].sort_values("timestamp").reset_index(drop=True)

    # --- 4. ENGINE + REPORTER (компоненты бота из контейнера) ---
    engine = container.trading_engine
    reporter = BacktestReporter(initial_balance=100.0)

    print(f"Starting backtest: {len(test_ts)} candles, {test_ts[0]} → {test_ts[-1]}")
    print(f"Symbols: {', '.join(all_dfs.keys())}")

    # --- 5. MAIN LOOP: feed candles → engine (тот же flow что live) ---
    for i in range(len(test_ts) - 1):
        current_ts = test_ts[i]
        next_ts = test_ts[i + 1]
        reporter.record_equity(container.portfolio.balance, current_ts)

        for sym, df in all_dfs.items():
            next_row = df.iloc[i + 1]

            # Engine делает ВСЁ: exit check → signal → entry
            trade_result = await engine.on_candle(
                symbol=sym,
                df=df,
                row_index=i,
                next_open=next_row["open"],
                next_high=next_row["high"],
                next_low=next_row["low"],
                next_ts=next_ts,
            )

            if trade_result:
                reporter.record_trade(trade_result, next_ts)
                container.trade_repo.save_trade({
                    "symbol": trade_result.symbol,
                    "side": trade_result.side.value,
                    "entry_price": trade_result.entry_price,
                    "exit_price": trade_result.exit_price,
                    "quantity": trade_result.quantity,
                    "notional": trade_result.notional,
                    "margin": trade_result.margin,
                    "raw_pnl_pct": trade_result.raw_pnl_pct,
                    "net_pnl_pct": trade_result.net_pnl_pct,
                    "pnl_abs": trade_result.pnl_abs,
                    "commission": trade_result.commission,
                    "reason": trade_result.reason,
                    "entry_time": trade_result.entry_time,
                    "exit_time": trade_result.exit_time,
                })

    # --- 6. REPORT ---
    reporter.print_summary(container.portfolio)


if __name__ == "__main__":
    asyncio.run(run_backtest())
