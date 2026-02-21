import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Optional
import math

import aiohttp
import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
ORDER_URL = "https://fapi.binance.com/fapi/v1/order"
BALANCE_URL = "https://fapi.binance.com/fapi/v2/balance"
POSITION_RISK_URL = "https://fapi.binance.com/fapi/v2/positionRisk"
TIME_URL = "https://fapi.binance.com/fapi/v1/time"

MAX_RETRIES = 5
BASE_BACKOFF = 1.0


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
        self.symbols_info = {}
        self.time_offset: Optional[int] = None

    async def sync_time(self) -> None:
        """Синхронизировать локальное время с сервером Binance."""
        session = self._ensure_session()
        try:
            async with session.get(TIME_URL, timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                data = await r.json()
                server_time = data["serverTime"]
                local_time = int(time.time() * 1000)
                self.time_offset = server_time - local_time
                logger.info(f"Binance time synced. Offset: {self.time_offset}ms")
        except Exception as e:
            logger.error(f"Failed to sync time: {e}")
            self.time_offset = 0

    async def _get_sys_time(self) -> int:
        if self.time_offset is None:
            await self.sync_time()
        return int(time.time() * 1000) + self.time_offset

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: int = MAX_RETRIES,
        retry_on_status: tuple = (429, 500, 502, 503, 504),
        **kwargs,
    ) -> dict:
        """Внутренний метод для выполнения запросов с retry policy."""
        session = self._ensure_session()
        for attempt in range(max_retries + 1):
            try:
                async with getattr(session, method.lower())(url, **kwargs) as r:
                    if r.status in retry_on_status and attempt < max_retries:
                        backoff = BASE_BACKOFF * (2 ** attempt)
                        logger.warning(
                            f"Binance API {r.status} on {url}. Retrying in {backoff}s "
                            f"(Attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(backoff)
                        continue
                        
                    r.raise_for_status()
                    return await r.json()
            except aiohttp.ClientResponseError as e:
                if e.status in retry_on_status and attempt < max_retries:
                    backoff = BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        f"Binance API Error {e.status} on {url}. Retrying in {backoff}s "
                        f"(Attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.error(f"Request failed explicitly: {e}")
                raise
            except asyncio.TimeoutError:
                if attempt < max_retries:
                    backoff = BASE_BACKOFF * (2 ** attempt)
                    logger.warning(
                        f"Binance API timeout on {url}. Retrying in {backoff}s "
                        f"(Attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.error(f"Request timed out.")
                raise
            except Exception as e:
                logger.error(f"Unexpected error on {url}: {e}")
                raise
        raise RuntimeError(f"Max retries ({max_retries}) exceeded for {url}")

    def _sign_params(self, params: dict) -> dict:
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_params = dict(params)
        signed_params["signature"] = signature
        return signed_params

    async def _fetch_order_by_client_id(self, api_symbol: str, client_order_id: str) -> Optional[dict]:
        """Проверить, был ли ордер создан, по clientOrderId."""
        if not self.api_key or not self.api_secret:
            return None
        try:
            timestamp = await self._get_sys_time()
            params = {
                "symbol": api_symbol,
                "origClientOrderId": client_order_id,
                "recvWindow": 5000,
                "timestamp": str(timestamp),
            }
            signed_params = self._sign_params(params)
            return await self._request_with_retry(
                "GET", ORDER_URL, params=signed_params, timeout=aiohttp.ClientTimeout(total=10)
            )
        except Exception as e:
            logger.warning(f"Failed to fetch order by clientOrderId: {e}")
            return None

    async def load_exchange_info(self) -> None:
        """Загрузить правила торговли (lot size, tick size и т.д.) для всех символов."""
        if self.symbols_info:
            return

        session = self._ensure_session()
        try:
            async with session.get(EXCHANGE_INFO_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()
                data = await r.json()
                
                for s_data in data.get("symbols", []):
                    sym = s_data["symbol"]
                    info = {
                        "stepSize": 0.0,
                        "minQty": 0.0,
                        "tickSize": 0.0,
                        "minNotional": 0.0,
                    }
                    for f in s_data.get("filters", []):
                        if f["filterType"] == "LOT_SIZE":
                            info["stepSize"] = float(f["stepSize"])
                            info["minQty"] = float(f["minQty"])
                        elif f["filterType"] == "PRICE_FILTER":
                            info["tickSize"] = float(f["tickSize"])
                        elif f["filterType"] == "MIN_NOTIONAL":
                            info["minNotional"] = float(f.get("notional", 0.0))
                    self.symbols_info[sym] = info
            logger.info(f"Loaded exchange info for {len(self.symbols_info)} symbols.")
        except Exception as e:
            logger.error(f"Failed to load exchange info: {e}")

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
                data = await self._request_with_retry(
                    "GET", BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
                )
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
        reduce_only: bool = False,
    ) -> Optional[dict]:
        """
        Размещение ордера через Binance Futures API.
        Подпись HMAC-SHA256.
        """
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API key/secret are required for live trading.")
        await self.load_exchange_info()
        session = self._ensure_session()
        api_symbol = symbol.replace("/", "")

        sym_info = self.symbols_info.get(api_symbol)
        qty_str = f"{quantity:.6f}"

        if sym_info:
            step_size = sym_info["stepSize"]
            tick_size = sym_info["tickSize"]
            min_qty = sym_info["minQty"]
            min_notional = sym_info["minNotional"]
            
            if step_size > 0:
                quantity = math.floor(quantity / step_size) * step_size
                qty_precision = max(0, int(round(-math.log10(step_size))))
                qty_str = f"{quantity:.{qty_precision}f}"
                
            if tick_size > 0:
                price = round(price / tick_size) * tick_size
                
            if quantity <= 0 or quantity < min_qty:
                raise ValueError(f"Quantity {quantity} is less than minQty {min_qty} for {symbol}")
                
            notional = quantity * price
            if min_notional > 0 and notional < min_notional:
                logger.warning(
                    f"Notional {notional} is less than minNotional {min_notional} for {symbol}. "
                    "Order not sent."
                )
                return None

        timestamp = await self._get_sys_time()
        client_order_id = hashlib.md5(f"{symbol}{timestamp}".encode("utf-8")).hexdigest()

        params = {
            "symbol": api_symbol,
            "side": side,
            "type": order_type,
            "quantity": qty_str,
            "newClientOrderId": client_order_id,
            "recvWindow": 5000,
            "timestamp": str(timestamp),
        }

        if reduce_only:
            params["reduceOnly"] = "true"

        signed_params = self._sign_params(params)

        try:
            data = await self._request_with_retry(
                "POST",
                ORDER_URL,
                params=signed_params,
                timeout=aiohttp.ClientTimeout(total=10),
                max_retries=0,
            )
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            existing = await self._fetch_order_by_client_id(api_symbol, client_order_id)
            if existing:
                data = existing
            else:
                raise

        fill_price = float(data.get("avgPrice", 0.0))
        exchange_order_id = str(data.get("orderId", ""))

        # Если avgPrice == 0.0 (часто бывает для MARKET ордеров), запрашиваем статус ордера
        if fill_price == 0.0 and exchange_order_id:
            await asyncio.sleep(0.5)  # Небольшая пауза, чтобы биржа успела обновить статус

            check_timestamp = await self._get_sys_time()
            check_params = {
                "symbol": api_symbol,
                "orderId": exchange_order_id,
                "recvWindow": 5000,
                "timestamp": str(check_timestamp),
            }
            signed_check_params = self._sign_params(check_params)

            try:
                order_status_data = await self._request_with_retry(
                    "GET", ORDER_URL, params=signed_check_params, timeout=aiohttp.ClientTimeout(total=10)
                )
                data = order_status_data
                fill_price = float(data.get("avgPrice", 0.0))
            except Exception as e:
                logger.warning(f"Failed to fetch order status for avgPrice, using requested price: {e}")
                fill_price = price

        if fill_price == 0.0:
            fill_price = price

        return {
            "fill_price": fill_price,
            "fill_quantity": float(data.get("executedQty", quantity)),
            "commission": 0.0,  # Binance returns this in separate endpoint
            "exchange_order_id": exchange_order_id,
        }

    async def get_balance(self) -> float:
        """Получить баланс USDT."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API key/secret are required for live trading.")
        session = self._ensure_session()

        timestamp = await self._get_sys_time()
        params = {
            "recvWindow": 5000,
            "timestamp": str(timestamp)
        }
        signed_params = self._sign_params(params)

        try:
            data = await self._request_with_retry(
                "GET", BALANCE_URL, params=signed_params, timeout=aiohttp.ClientTimeout(total=10)
            )
            for asset in data:
                if asset["asset"] == "USDT":
                    return float(asset["balance"])
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """Получить позиции с Binance Futures (positionRisk)."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API key/secret are required for live trading.")

        timestamp = await self._get_sys_time()
        params = {
            "recvWindow": 5000,
            "timestamp": str(timestamp),
        }

        signed_params = self._sign_params(params)

        try:
            data = await self._request_with_retry(
                "GET", POSITION_RISK_URL, params=signed_params, timeout=aiohttp.ClientTimeout(total=10)
            )
        except Exception as e:
            logger.error(f"Failed to get position risk: {e}")
            raise

        if isinstance(data, dict):
            data = [data]

        symbols_set = {s.replace("/", "") for s in symbols} if symbols else None
        results = []
        for item in data:
            api_symbol = item.get("symbol")
            if not api_symbol:
                continue
            if symbols_set and api_symbol not in symbols_set:
                continue
            results.append({
                "symbol": api_symbol,
                "quantity": float(item.get("positionAmt", 0.0)),
                "entry_price": float(item.get("entryPrice", 0.0)),
                "leverage": int(float(item.get("leverage", self.settings.LEVERAGE))),
                "mark_price": float(item.get("markPrice", 0.0)),
            })

        return results

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()