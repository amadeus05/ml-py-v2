"""
LiveRunner — WebSocket-based live/paper trading loop.

Single stream per symbol:
  1. Main timeframe (e.g. 1h) — on candle close → ML analysis + entry

Uses aiohttp for WebSocket connections to Binance Futures.
"""
import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp
import pandas as pd

from trading_engine.adapters.config.settings import Settings
from trading_engine.bootstrap.container import Container

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://fstream.binance.com/ws"


class LiveRunner:
    """
    Orchestrates the live trading loop.

    Flow:
        1. Fetch initial candle history (REST) for feature warmup
        2. Connect to WebSocket kline streams:
           - Main TF: candle close → features → ML predict → entry
        3. Run until shutdown signal (Ctrl+C)
    """

    def __init__(self, container: Container, settings: Settings):
        self.container = container
        self.settings = settings
        self.engine = container.trading_engine
        self.position_manager = container.position_manager
        self.execution_service = container.execution_service
        self.signal_handler = container.signal_handler
        self.feature_engine = container.feature_engine
        self.exchange = container.exchange
        self.notifier = container.notifier

        # In-memory candle buffers per symbol
        self._main_tf_buffers: Dict[str, pd.DataFrame] = {}
        self._htf_buffers: Dict[str, pd.DataFrame] = {}

        # Shutdown control
        self._shutdown_event = asyncio.Event()
        self._ws_tasks: list[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────
    async def run(self) -> None:
        """Main entry point — blocks until shutdown."""
        logger.info("=" * 60)
        logger.info("  LIVE RUNNER STARTING")
        logger.info(f"  Mode: {self.settings.EXCHANGE_MODE}")
        logger.info(f"  Symbols: {self.settings.SYMBOLS}")
        logger.info(f"  Main TF: {self.settings.TIMEFRAME}")
        logger.info("  SL/TP monitor: exchange-side orders")
        logger.info("=" * 60)

        # Register shutdown signals
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        self._session = aiohttp.ClientSession()

        try:
            # Step 1: Load initial history for feature computation
            await self._load_all_history()

            # Step 2: Reconcile positions with exchange
            await self._reconcile_positions(label="startup")

            # Step 3: Notify start
            open_pos = list(self.position_manager.positions.keys())
            await self.notifier.notify(
                f"🤖 Bot started in {self.settings.EXCHANGE_MODE} mode\n"
                f"Symbols: {', '.join(self.settings.SYMBOLS)}\n"
                f"TF: {self.settings.TIMEFRAME} | SL/TP on exchange\n"
                f"Open positions: {open_pos if open_pos else 'none'}"
            )

            # Step 4: Launch WS streams for each symbol
            for symbol in self.settings.SYMBOLS:
                ws_symbol = symbol.replace("/", "").lower()

                # Main timeframe stream
                task_main = asyncio.create_task(
                    self._ws_kline_loop(
                        symbol=symbol,
                        ws_symbol=ws_symbol,
                        interval=self.settings.TIMEFRAME,
                        handler=self._on_main_candle_close,
                        stream_name=f"{symbol} main-tf",
                    ),
                    name=f"ws-main-{symbol}",
                )
                self._ws_tasks.append(task_main)

            # Step 5: Launch periodic reconciliation
            if self.settings.RECONCILE_ENABLED:
                reconcile_task = asyncio.create_task(
                    self._periodic_reconcile_loop(),
                    name="periodic-reconcile",
                )
                self._ws_tasks.append(reconcile_task)

            # Step 6: Wait for shutdown
            logger.info("All WebSocket streams launched. Waiting for signals...")
            await self._shutdown_event.wait()

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
        finally:
            await self._cleanup()

    # ──────────────────────────────────────────────
    # WebSocket Loop (generic for any interval)
    # ──────────────────────────────────────────────
    async def _ws_kline_loop(
        self,
        symbol: str,
        ws_symbol: str,
        interval: str,
        handler,
        stream_name: str,
    ) -> None:
        """
        Persistent WebSocket connection with auto-reconnect.
        Listens for kline events and calls handler on candle close.
        """
        url = f"{BINANCE_WS_BASE}/{ws_symbol}@kline_{interval}"
        reconnect_delay = self.settings.WS_RECONNECT_DELAY

        while not self._shutdown_event.is_set():
            try:
                logger.info(f"[{stream_name}] Connecting to {url}")
                async with self._session.ws_connect(url, heartbeat=20) as ws:
                    logger.info(f"[{stream_name}] Connected ✓")

                    async for msg in ws:
                        if self._shutdown_event.is_set():
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            kline = data.get("k", {})

                            # Only process on candle CLOSE (x == true)
                            if kline.get("x", False):
                                try:
                                    await handler(symbol, kline)
                                except Exception as e:
                                    logger.error(
                                        f"[{stream_name}] Handler error: {e}",
                                        exc_info=True,
                                    )

                        elif msg.type in (
                            aiohttp.WSMsgType.ERROR,
                            aiohttp.WSMsgType.CLOSED,
                        ):
                            logger.warning(f"[{stream_name}] WS closed/error: {msg}")
                            break

            except asyncio.CancelledError:
                logger.info(f"[{stream_name}] Cancelled")
                return
            except Exception as e:
                logger.error(f"[{stream_name}] Connection error: {e}")

            if not self._shutdown_event.is_set():
                logger.info(f"[{stream_name}] Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)

    # ──────────────────────────────────────────────
    # Handler: Main Timeframe Candle Close
    # ──────────────────────────────────────────────
    async def _on_main_candle_close(self, symbol: str, kline: dict) -> None:
        """
        Called when a main-TF candle closes.
        Appends the new candle, recomputes features, and runs ML pipeline.
        """
        new_candle = self._parse_kline(kline)
        logger.info(
            f"[{symbol}] Main TF candle closed: "
            f"{new_candle['timestamp']} | O={new_candle['open']:.2f} "
            f"H={new_candle['high']:.2f} L={new_candle['low']:.2f} "
            f"C={new_candle['close']:.2f} V={new_candle['volume']:.0f}"
        )

        # Append to buffer
        df = self._main_tf_buffers.get(symbol, pd.DataFrame())
        new_row = pd.DataFrame([new_candle])
        df = pd.concat([df, new_row], ignore_index=True)

        # Keep buffer trimmed (last N candles for feature computation)
        max_rows = self.settings.INITIAL_HISTORY_CANDLES + 50
        if len(df) > max_rows:
            df = df.iloc[-max_rows:].reset_index(drop=True)
        self._main_tf_buffers[symbol] = df

        # Compute features
        htf_df = self._htf_buffers.get(symbol)
        featured_df = self.feature_engine.transform(df.copy(), htf_df=htf_df)

        if featured_df.empty:
            logger.warning(f"[{symbol}] Feature computation returned empty DataFrame")
            return

        # Current candle = last row, use it for signal
        row_index = len(featured_df) - 1
        current_close = new_candle["close"]
        current_ts = new_candle["timestamp"]

        # В Live режиме:
        # 1. Свеча уже закрыта, поэтому next_open для engine это цена закрытия текущей свечи (current_close).
        # 2. next_high/next_low передаем как current_close — SL/TP контролируются биржей.
        
        trade_result = await self.engine.on_candle(
            symbol=symbol,
            df=featured_df,
            row_index=row_index,
            next_open=current_close,
            next_high=current_close,
            next_low=current_close,
            next_ts=current_ts,
        )

        if trade_result:
            logger.info(
                f"[{symbol}] EXIT by Engine ({trade_result.reason}) at {trade_result.exit_price:.2f} "
                f"PnL: {trade_result.net_pnl_pct * 100:.2f}%"
            )

        if hasattr(self.exchange, "process_candle"):
            try:
                self.exchange.process_candle(
                    symbol=symbol,
                    open_=new_candle["open"],
                    high=new_candle["high"],
                    low=new_candle["low"],
                    close=new_candle["close"],
                )
            except Exception as e:
                logger.warning(f"[{symbol}] Failed to process candle in exchange: {e}")

    # ──────────────────────────────────────────────
    # Reconciliation
    # ──────────────────────────────────────────────
    async def _reconcile_positions(self, label: str = "periodic") -> None:
        """
        Fetch open positions & balance from exchange and sync local state.
        Safe to call at startup and periodically.
        """
        try:
            # 1. Get exchange positions
            raw_positions = await self.exchange.get_position_risk(
                symbols=self.settings.SYMBOLS
            )

            # 2. Convert API symbols → local symbols and build dict
            exchange_positions: dict[str, dict] = {}
            for pos in raw_positions:
                api_sym = pos.get("symbol", "")
                local_sym = self._api_symbol_to_local(api_sym)
                qty = pos.get("quantity", 0.0)
                if abs(qty) <= self.settings.RECONCILE_QTY_TOLERANCE:
                    continue
                exchange_positions[local_sym] = {
                    "quantity": qty,
                    "entry_price": pos.get("entry_price", 0.0),
                    "leverage": pos.get("leverage", self.settings.LEVERAGE),
                    "mark_price": pos.get("mark_price", 0.0),
                }

            # 3. Reconcile local positions
            changes = self.position_manager.reconcile_with_exchange(
                exchange_positions,
                qty_tolerance=self.settings.RECONCILE_QTY_TOLERANCE,
            )

            # 4. Sync balance from exchange
            exchange_balance = await self.exchange.get_balance()
            if exchange_balance > 0:
                old_balance = self.container.portfolio.balance
                self.container.portfolio.balance = exchange_balance
                if abs(old_balance - exchange_balance) > 0.01:
                    logger.info(
                        f"[reconcile:{label}] Balance synced: "
                        f"{old_balance:.2f} → {exchange_balance:.2f}"
                    )

            # 5. Log results
            if changes["created"] or changes["updated"] or changes["closed"]:
                logger.warning(
                    f"[reconcile:{label}] Changes: "
                    f"created={changes['created']}, "
                    f"updated={changes['updated']}, "
                    f"closed={changes['closed']}"
                )
            else:
                logger.info(f"[reconcile:{label}] Positions in sync ✓")

        except Exception as e:
            logger.error(
                f"[reconcile:{label}] Failed: {e}", exc_info=True
            )

    async def _periodic_reconcile_loop(self) -> None:
        """Background loop: reconcile every RECONCILE_INTERVAL_SEC seconds."""
        interval = self.settings.RECONCILE_INTERVAL_SEC
        logger.info(f"Periodic reconciliation started (every {interval}s)")

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=interval
                )
                # If we get here, shutdown was requested
                break
            except asyncio.TimeoutError:
                # Timeout = interval elapsed, time to reconcile
                await self._reconcile_positions(label="periodic")

        logger.info("Periodic reconciliation stopped")

    @staticmethod
    def _api_symbol_to_local(api_symbol: str) -> str:
        """
        Convert API symbol format to local format.
        ETHUSDT → ETH/USDT, BTCUSDT → BTC/USDT
        """
        for quote in ("USDT", "BUSD", "USDC"):
            if api_symbol.endswith(quote):
                base = api_symbol[: -len(quote)]
                return f"{base}/{quote}"
        return api_symbol

    # ──────────────────────────────────────────────
    # Initial History Loading
    # ──────────────────────────────────────────────
    async def _load_all_history(self) -> None:
        """Fetch initial candle history for all symbols (REST API)."""
        logger.info("Loading initial candle history for feature warmup...")

        for symbol in self.settings.SYMBOLS:
            try:
                await self._load_symbol_history(symbol)
            except Exception as e:
                logger.error(f"Failed to load history for {symbol}: {e}", exc_info=True)

    async def _load_symbol_history(self, symbol: str) -> None:
        """Load main TF + HTF history for one symbol."""
        limit = self.settings.INITIAL_HISTORY_CANDLES

        # Calculate 'since' — we only need the last N candles from now
        tf_ms = self._timeframe_to_ms(self.settings.TIMEFRAME)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        since_ms = now_ms - (limit * tf_ms)

        # Main timeframe
        df_main = await self.exchange.fetch_ohlcv(
            symbol=symbol,
            timeframe=self.settings.TIMEFRAME,
            since=since_ms,
            limit=limit,
        )
        if df_main.empty:
            logger.warning(f"[{symbol}] No main TF history loaded")
            return

        # Ensure timestamp column
        if "timestamp" not in df_main.columns and "open_time_ms" in df_main.columns:
            df_main["timestamp"] = pd.to_datetime(df_main["open_time_ms"], unit="ms")

        self._main_tf_buffers[symbol] = df_main
        logger.info(f"[{symbol}] Loaded {len(df_main)} candles ({self.settings.TIMEFRAME})")

        # HTF (e.g. 4h)
        if self.settings.HTF_TIMEFRAME:
            htf_ms = self._timeframe_to_ms(self.settings.HTF_TIMEFRAME)
            htf_since_ms = now_ms - (limit * htf_ms)
            df_htf = await self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=self.settings.HTF_TIMEFRAME,
                since=htf_since_ms,
                limit=limit,
            )
            if not df_htf.empty:
                if "timestamp" not in df_htf.columns and "open_time_ms" in df_htf.columns:
                    df_htf["timestamp"] = pd.to_datetime(df_htf["open_time_ms"], unit="ms")
                self._htf_buffers[symbol] = df_htf
                logger.info(
                    f"[{symbol}] Loaded {len(df_htf)} HTF candles ({self.settings.HTF_TIMEFRAME})"
                )

    # ──────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────
    @staticmethod
    def _timeframe_to_ms(tf: str) -> int:
        """Convert timeframe string to milliseconds."""
        mapping = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        if tf not in mapping:
            raise ValueError(f"Unknown timeframe: {tf}")
        return mapping[tf]

    @staticmethod
    def _parse_kline(kline: dict) -> dict:
        """Parse a Binance kline WebSocket message into a candle dict."""
        return {
            "timestamp": pd.to_datetime(int(kline["t"]), unit="ms"),
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "quote_volume": float(kline["q"]),
            "open_time_ms": int(kline["t"]),
        }

    def _request_shutdown(self) -> None:
        """Signal handler — request graceful shutdown."""
        logger.info("Shutdown requested...")
        self._shutdown_event.set()

    async def _cleanup(self) -> None:
        """Cancel all tasks and close connections."""
        logger.info("Cleaning up...")

        # Cancel WS tasks
        for task in self._ws_tasks:
            task.cancel()
        if self._ws_tasks:
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)

        # Close aiohttp session
        if self._session and not self._session.closed:
            await self._session.close()

        # Close exchange
        try:
            await self.exchange.close()
        except Exception:
            pass

        # Notify shutdown
        try:
            await self.notifier.notify("🛑 Bot stopped")
        except Exception:
            pass

        logger.info("LiveRunner shutdown complete")
