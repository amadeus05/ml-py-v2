import asyncio
import hashlib
import hmac
import logging
import time
import math
from datetime import datetime
from typing import Optional

import aiohttp
import pandas as pd

from trading_engine.interfaces.IExchange import IExchange
from trading_engine.adapters.config.settings import Settings

logger = logging.getLogger(__name__)

# ── Bybit V5 Unified API endpoints ──────────────────────────────────────────
BASE_V5 = "https://api.bybit.com"
KLINE_URL = f"{BASE_V5}/v5/market/kline"
ORDER_URL = f"{BASE_V5}/v5/order/create"
ORDER_QUERY_URL = f"{BASE_V5}/v5/order/realtime"
WALLET_URL = f"{BASE_V5}/v5/account/wallet-balance"
POSITION_URL = f"{BASE_V5}/v5/position/list"
INSTRUMENTS_URL = f"{BASE_V5}/v5/market/instruments-info"
TIME_URL = f"{BASE_V5}/v5/market/time"

MAX_RETRIES = 5
BASE_BACKOFF = 1.0


class BybitExchange(IExchange):
    """
    Bybit Futures (linear USDT perpetual) exchange adapter (fully async via aiohttp).

    fetch_ohlcv — загрузка OHLCV свечей через Bybit V5 Market API.
    place_order — Bybit V5 Trade API (linear market order).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.BYBIT_API_KEY
        self.api_secret = settings.BYBIT_API_SECRET
        self._session: Optional[aiohttp.ClientSession] = None
        self.symbols_info: dict = {}
        self.time_offset: Optional[int] = None

    # ── Time synchronisation ────────────────────────────────────────────────

    async def sync_time(self) -> None:
        """Синхронизировать локальное время с сервером Bybit."""
        session = self._ensure_session()
        try:
            async with session.get(TIME_URL, timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                data = await r.json()
                server_time = int(data["result"]["timeSecond"]) * 1000
                local_time = int(time.time() * 1000)
                self.time_offset = server_time - local_time
                logger.info(f"Bybit time synced. Offset: {self.time_offset}ms")
        except Exception as e:
            logger.error(f"Failed to sync Bybit time: {e}")
            self.time_offset = 0

    async def _get_sys_time(self) -> int:
        if self.time_offset is None:
            await self.sync_time()
        return int(time.time() * 1000) + self.time_offset

    # ── HTTP helpers ────────────────────────────────────────────────────────

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
                            f"Bybit API {r.status} on {url}. Retrying in {backoff}s "
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
                        f"Bybit API Error {e.status} on {url}. Retrying in {backoff}s "
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
                        f"Bybit API timeout on {url}. Retrying in {backoff}s "
                        f"(Attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.error("Request timed out.")
                raise
            except Exception as e:
                logger.error(f"Unexpected error on {url}: {e}")
                raise
        raise RuntimeError(f"Max retries ({max_retries}) exceeded for {url}")

    # ── Signing (Bybit V5 HMAC-SHA256) ──────────────────────────────────────

    def _sign_request(self, timestamp: int, params_str: str) -> str:
        """
        Bybit V5 подпись:  HMAC_SHA256(timestamp + api_key + recvWindow + queryString/body)
        """
        recv_window = "5000"
        pre_sign = f"{timestamp}{self.api_key}{recv_window}{params_str}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            pre_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, timestamp: int, params_str: str) -> dict:
        """Возвращает заголовки авторизации для Bybit V5."""
        sign = self._sign_request(timestamp, params_str)
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type": "application/json",
        }

    # ── Session management ──────────────────────────────────────────────────

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Lazily create the aiohttp session (must be called inside a running loop)."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ── Exchange info (instrument filters) ──────────────────────────────────

    async def load_exchange_info(self) -> None:
        """Загрузить правила торговли (lot size, tick size и т.д.) для линейных инструментов."""
        if self.symbols_info:
            return

        session = self._ensure_session()
        cursor = ""
        try:
            while True:
                params = {"category": "linear", "limit": "1000"}
                if cursor:
                    params["cursor"] = cursor

                async with session.get(
                    INSTRUMENTS_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    r.raise_for_status()
                    data = await r.json()

                result = data.get("result", {})
                for item in result.get("list", []):
                    sym = item["symbol"]
                    lot_filter = item.get("lotSizeFilter", {})
                    price_filter = item.get("priceFilter", {})
                    info = {
                        "stepSize": float(lot_filter.get("qtyStep", 0.0)),
                        "minQty": float(lot_filter.get("minOrderQty", 0.0)),
                        "tickSize": float(price_filter.get("tickSize", 0.0)),
                        "minNotional": 0.0,  # Bybit не имеет minNotional фильтра как Binance
                    }
                    self.symbols_info[sym] = info

                cursor = result.get("nextPageCursor", "")
                if not cursor:
                    break

            logger.info(f"Loaded Bybit exchange info for {len(self.symbols_info)} symbols.")
        except Exception as e:
            logger.error(f"Failed to load Bybit exchange info: {e}")

    # ── OHLCV ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tf_to_bybit(timeframe: str) -> str:
        """Конвертировать таймфрейм вида '1h' → Bybit V5 формат '60'."""
        mapping = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        result = mapping.get(timeframe)
        if result is None:
            raise ValueError(f"Unsupported timeframe for Bybit: {timeframe}")
        return result

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """
        Загрузка OHLCV — аналогично BinanceExchange.fetch_ohlcv().
        Поддерживает пагинацию и инкрементальную загрузку.
        Bybit V5 /v5/market/kline максимум 1000 свечей за запрос, отдаёт DESC.
        """
        session = self._ensure_session()
        api_symbol = symbol.replace("/", "")
        bybit_tf = self._tf_to_bybit(timeframe)

        if since is None:
            since = int(datetime.fromisoformat(self.settings.START_DATE).timestamp() * 1000)

        end_ts = None
        if self.settings.END_DATE:
            end_ts = int(datetime.fromisoformat(self.settings.END_DATE).timestamp() * 1000)

        if end_ts and since >= end_ts:
            logger.info(f"[{symbol}-{timeframe}] Data already loaded up to {self.settings.END_DATE}")
            return pd.DataFrame()

        if limit > 1000:
            limit = 1000

        all_rows: list[dict] = []

        while True:
            params = {
                "category": "linear",
                "symbol": api_symbol,
                "interval": bybit_tf,
                "start": str(since),
                "limit": str(limit),
            }
            if end_ts:
                params["end"] = str(end_ts)

            try:
                data = await self._request_with_retry(
                    "GET", KLINE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
                )
            except Exception as e:
                logger.error(f"Error fetching {symbol}-{timeframe} from Bybit: {e}")
                break

            result = data.get("result", {})
            candles = result.get("list", [])

            if not candles:
                break

            # Bybit V5 возвращает свечи в обратном порядке (DESC), реверсируем
            candles.reverse()

            for k in candles:
                # [startTime, open, high, low, close, volume, turnover]
                current_ts = int(k[0])

                if end_ts and current_ts >= end_ts:
                    continue

                all_rows.append({
                    "timestamp": pd.to_datetime(current_ts, unit="ms"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "quote_volume": float(k[6]),
                    "open_time_ms": current_ts,
                })

            # last candle timestamp → следующий since
            last_ts = int(candles[-1][0])
            since = last_ts + 1

            logger.info(
                f"[{symbol}-{timeframe}] Loaded {len(all_rows)} candles from Bybit, "
                f"up to {datetime.fromtimestamp(last_ts / 1000)}"
            )

            if len(candles) < limit:
                break
            if end_ts and since >= end_ts:
                break

            await asyncio.sleep(self.settings.BINANCE_SLEEP)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        return df

    # ── Order placement ─────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "MARKET",
        reduce_only: bool = False,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Размещение ордера через Bybit V5 Trade API.
        Подпись HMAC-SHA256 через заголовки.
        """
        if not self.api_key or not self.api_secret:
            raise ValueError("Bybit API key/secret are required for live trading.")
        await self.load_exchange_info()
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

        # Bybit V5 side: "Buy" / "Sell" (capitalized)
        bybit_side = side.capitalize() if side.upper() in ("BUY", "SELL") else side
        order_type_upper = order_type.upper()
        is_trigger_order = order_type_upper in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
        bybit_order_type = "Market" if order_type_upper == "MARKET" or is_trigger_order else "Limit"
        trigger_price = stop_price if stop_price is not None else price

        timestamp = await self._get_sys_time()

        import json as _json
        body = {
            "category": "linear",
            "symbol": api_symbol,
            "side": bybit_side,
            "orderType": bybit_order_type,
            "qty": qty_str,
            "timeInForce": "GTC",
            "positionIdx": 0,  # One-way mode (0=one-way, 1=hedge-long, 2=hedge-short)
        }

        if reduce_only:
            body["reduceOnly"] = True
        if is_trigger_order:
            body["triggerPrice"] = f"{trigger_price:.6f}"
            body["triggerBy"] = "LastPrice"
            if order_type_upper == "STOP_MARKET":
                body["stopLoss"] = f"{trigger_price:.6f}"
                body["triggerDirection"] = 2 if bybit_side == "Sell" else 1
            else:
                body["takeProfit"] = f"{trigger_price:.6f}"
                body["triggerDirection"] = 1 if bybit_side == "Sell" else 2
        else:
            if take_profit is not None:
                body["takeProfit"] = f"{take_profit:.6f}"
            if stop_loss is not None:
                body["stopLoss"] = f"{stop_loss:.6f}"
        body_str = _json.dumps(body, separators=(",", ":"))
        headers = self._auth_headers(timestamp, body_str)

        try:
            data = await self._request_with_retry(
                "POST",
                ORDER_URL,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
                max_retries=0,
            )
        except Exception as e:
            logger.error(f"Bybit order placement failed: {e}")
            raise

        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            msg = data.get("retMsg", "unknown error")
            logger.error(f"Bybit order rejected: retCode={ret_code}, retMsg={msg}")
            raise RuntimeError(f"Bybit order rejected: {msg}")

        result = data.get("result", {})
        exchange_order_id = result.get("orderId", "")

        # Запрашиваем подробности ордера, чтобы получить avgPrice
        fill_price = 0.0
        fill_quantity = float(qty_str)

        if exchange_order_id:
            await asyncio.sleep(0.5)  # Небольшая пауза, чтобы биржа заполнила ордер

            try:
                query_ts = await self._get_sys_time()
                query_params = {
                    "category": "linear",
                    "symbol": api_symbol,
                    "orderId": exchange_order_id,
                }
                import urllib.parse
                query_string = urllib.parse.urlencode(query_params)
                query_headers = self._auth_headers(query_ts, query_string)

                order_data = await self._request_with_retry(
                    "GET",
                    ORDER_QUERY_URL,
                    params=query_params,
                    headers=query_headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                order_list = order_data.get("result", {}).get("list", [])
                if order_list:
                    order_info = order_list[0]
                    fill_price = float(order_info.get("avgPrice", 0.0))
                    cum_qty = float(order_info.get("cumExecQty", 0.0))
                    if cum_qty > 0:
                        fill_quantity = cum_qty
            except Exception as e:
                logger.warning(f"Failed to fetch Bybit order status for avgPrice: {e}")
                fill_price = price

        if fill_price == 0.0:
            fill_price = price

        return {
            "fill_price": fill_price,
            "fill_quantity": fill_quantity,
            "commission": 0.0,  # Bybit returns commission in separate endpoint
            "exchange_order_id": exchange_order_id,
        }

    # ── Balance ─────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Получить баланс USDT (Unified Trading Account)."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Bybit API key/secret are required for live trading.")

        timestamp = await self._get_sys_time()
        import urllib.parse
        params = {"accountType": "UNIFIED", "coin": "USDT"}
        query_string = urllib.parse.urlencode(params)
        headers = self._auth_headers(timestamp, query_string)

        try:
            data = await self._request_with_retry(
                "GET",
                WALLET_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            )
            ret_code = data.get("retCode", -1)
            if ret_code != 0:
                raise RuntimeError(f"Bybit wallet error: {data.get('retMsg', 'unknown')}")

            accounts = data.get("result", {}).get("list", [])
            for acc in accounts:
                coins = acc.get("coin", [])
                for c in coins:
                    if c.get("coin") == "USDT":
                        return float(c.get("walletBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get Bybit balance: {e}")
            raise

    # ── Position risk ───────────────────────────────────────────────────────

    async def get_position_risk(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """Получить позиции с Bybit (linear position list)."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Bybit API key/secret are required for live trading.")

        timestamp = await self._get_sys_time()
        import urllib.parse
        params = {"category": "linear", "settleCoin": "USDT"}
        query_string = urllib.parse.urlencode(params)
        headers = self._auth_headers(timestamp, query_string)

        try:
            data = await self._request_with_retry(
                "GET",
                POSITION_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        except Exception as e:
            logger.error(f"Failed to get Bybit position risk: {e}")
            raise

        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise RuntimeError(f"Bybit position error: {data.get('retMsg', 'unknown')}")

        positions = data.get("result", {}).get("list", [])
        symbols_set = {s.replace("/", "") for s in symbols} if symbols else None

        results = []
        for item in positions:
            api_symbol = item.get("symbol", "")
            if not api_symbol:
                continue
            if symbols_set and api_symbol not in symbols_set:
                continue

            size = float(item.get("size", 0.0))
            side = item.get("side", "")
            # Bybit: side="Buy" → positive qty, side="Sell" → negative qty
            if side == "Sell":
                size = -size

            results.append({
                "symbol": api_symbol,
                "quantity": size,
                "entry_price": float(item.get("avgPrice", 0.0)),
                "leverage": int(float(item.get("leverage", self.settings.LEVERAGE))),
                "mark_price": float(item.get("markPrice", 0.0)),
            })

        return results

    # ── Cleanup ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
