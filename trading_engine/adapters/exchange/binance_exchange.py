import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Optional

import aiohttp
import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
ORDER_URL = "https://fapi.binance.com/fapi/v1/order"
BALANCE_URL = "https://fapi.binance.com/fapi/v2/balance"


class BinanceExchange(IExchange):
    """
    Binance Futures exchange adapter (fully async via aiohttp).

    fetch_ohlcv — 100% логика из MVP etl_pipeline.py fetch_data().
    place_order — Binance API (futures market order).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self._session: Optional[aiohttp.ClientSession] = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Lazily create the aiohttp session (must be called inside a running loop)."""
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["X-MBX-APIKEY"] = self.api_key
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """
        Загрузка OHLCV — 100% как MVP fetch_data().
        Поддерживает пагинацию и инкрементальную загрузку.
        """
        session = self._ensure_session()
        api_symbol = symbol.replace("/", "")

        if since is None:
            since = int(datetime.fromisoformat(self.settings.START_DATE).timestamp() * 1000)

        end_ts = None
        if self.settings.END_DATE:
            end_ts = int(datetime.fromisoformat(self.settings.END_DATE).timestamp() * 1000)

        if end_ts and since >= end_ts:
            logger.info(f"[{symbol}-{timeframe}] Data already loaded up to {self.settings.END_DATE}")
            return pd.DataFrame()

        all_rows = []

        while True:
            params = {
                "symbol": api_symbol,
                "interval": timeframe,
                "startTime": since,
                "limit": limit,
            }
            if end_ts:
                params["endTime"] = end_ts

            try:
                async with session.get(BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    data = await r.json()
            except Exception as e:
                logger.error(f"Error fetching {symbol}-{timeframe}: {e}")
                break

            if not data:
                break

            for k in data:
                current_ts = k[0]
                all_rows.append({
                    "timestamp": pd.to_datetime(current_ts, unit="ms"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "quote_volume": float(k[7]),
                    "open_time_ms": current_ts,
                })
                since = current_ts + 1

            logger.info(
                f"[{symbol}-{timeframe}] Loaded {len(all_rows)} candles, "
                f"up to {datetime.fromtimestamp((since - 1) / 1000)}"
            )

            if len(data) < limit:
                break
            if end_ts and since >= end_ts:
                break

            await asyncio.sleep(self.settings.BINANCE_SLEEP)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        return df

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
    ) -> dict:
        """
        Размещение ордера через Binance Futures API.
        Подпись HMAC-SHA256.
        """
        session = self._ensure_session()
        api_symbol = symbol.replace("/", "")

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": api_symbol,
            "side": side,
            "type": order_type,
            "quantity": f"{quantity:.6f}",
            "timestamp": str(timestamp),
        }

        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature

        try:
            async with session.post(ORDER_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()
                data = await r.json()

            return {
                "fill_price": float(data.get("avgPrice", price)),
                "fill_quantity": float(data.get("executedQty", quantity)),
                "commission": 0.0,  # Binance returns this in separate endpoint
                "exchange_order_id": str(data.get("orderId", "")),
            }
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    async def get_balance(self) -> float:
        """Получить баланс USDT."""
        session = self._ensure_session()

        timestamp = int(time.time() * 1000)
        params = {"timestamp": str(timestamp)}
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature

        try:
            async with session.get(BALANCE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()
                data = await r.json()
                for asset in data:
                    if asset["asset"] == "USDT":
                        return float(asset["balance"])
                return 0.0
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()