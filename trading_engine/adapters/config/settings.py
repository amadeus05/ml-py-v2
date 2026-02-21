from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    Все настройки проекта — объединение MVP config.py + bt.py constants.
    Загружаются из .env и/или environment variables.
    """

    # --- DATABASE ---
    DB_PATH: str = "market_data.db"

    # --- SYMBOLS ---
    SYMBOLS: List[str] = [
        # "BTC/USDT",
        "ETH/USDT",
        # "BNB/USDT",
        # "SOL/USDT",
        # "ADA/USDT",
        # "TRX/USDT",
        # "LINK/USDT",
        # "AVAX/USDT",
        # "DOT/USDT",
        # "ATOM/USDT"
    ]
    
    # --- TIMEFRAMES ---
    TIMEFRAME: str = "1h"
    HTF_TIMEFRAME: str = "4h"
    LABEL_TIMEFRAME: str = "1m"
    USE_1M_CANDLES: bool = False  # Отключает долгую загрузку 1m свечей для лейблинга

    # --- DATA LOADING ---
    START_DATE: str = "2022-01-01"
    END_DATE: Optional[str] = None
    BINANCE_LIMIT: int = 1500
    BINANCE_SLEEP: float = 0.3

    # --- ML TRAINING ---
    ENABLE_PROD_TRAINING: bool = False
    HORIZON: int = 12

    # --- ML LABELING (Triple Barrier) ---
    TP_PCT: float = 0.030       # +3% Take Profit
    SL_PCT: float = 0.015       # -1.5% Stop Loss
    MFE_THRESHOLD: float = 0.030

    # --- TRADING ---
    CONFIDENCE_THRESHOLD: float = 0.65
    LEVERAGE: int = 1
    RISK_PER_TRADE: float = 0.01
    MIN_NOTIONAL: float = 10.0

    # --- FEES (Binance Futures) ---
    TAKER_FEE: float = 0.0004
    MAKER_FEE: float = 0.0002
    SLIPPAGE: float = 0.0003

    # --- SLIDING WINDOW ---
    SLIDING_WINDOW: bool = True
    WINDOW_SIZE_DAYS: int = 60
    WINDOW_STEP_DAYS: int = 10

    # --- MODELS ---
    MODELS_DIR: str = "models"

    # --- EXCHANGE MODE ---
    EXCHANGE_MODE: str = "simulation"  # binance, bybit, paper, simulation

    # --- LIVE MODE ---
    INITIAL_HISTORY_CANDLES: int = 300  # Candles to fetch at startup for feature warmup
    WS_RECONNECT_DELAY: int = 5         # Seconds before WS reconnect

    # --- API KEYS ---
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""

    # --- TELEGRAM ---
    TG_API_KEY: str = ""
    TG_CHAT_ID: str = ""
    TG_ENABLED: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def models_path(self) -> Path:
        return Path(self.MODELS_DIR)
        
    def ensure_models_dir(self) -> None:
        self.models_path.mkdir(exist_ok=True, parents=True)