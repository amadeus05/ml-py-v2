import logging
from typing import Optional

from trading_engine.adapters.config.settings import Settings
from trading_engine.adapters.persistence.database import DatabaseManager
from trading_engine.adapters.persistence.repositories.candle_repository import SQLiteCandleRepository
from trading_engine.adapters.persistence.repositories.trade_repository import SQLiteTradeRepository
from trading_engine.adapters.exchange.binance_exchange import BinanceExchange
from trading_engine.adapters.exchange.paper_exchange import PaperExchange
from trading_engine.adapters.exchange.simulation_exchange import SimulationExchange
from trading_engine.adapters.exchange.bybit_exchange import BybitExchange
from trading_engine.adapters.notifications.telegram_notifier import TelegramNotifier
from trading_engine.adapters.notifications.null_notifier import NullNotifier
from trading_engine.adapters.ml.feature_engine import CryptoFeatureEngine
from trading_engine.adapters.ml.catboost_model import CatBoostMLModel

from trading_engine.core.domain.portfolio import Portfolio
from trading_engine.core.domain.risk_engine import RiskEngine
from trading_engine.core.application.position_manager import PositionManager
from trading_engine.core.application.risk_manager import RiskManager
from trading_engine.core.application.signal_handler import SignalHandler
from trading_engine.core.application.execution_service import ExecutionService
from trading_engine.core.application.trading_engine import TradingEngine
from trading_engine.interfaces.IExchange import IExchange
from trading_engine.interfaces.INotifier import INotifier

logger = logging.getLogger(__name__)


class Container:
    """
    DI Container — собирает все зависимости в зависимости от режима.
    
    Usage:
        settings = Settings()
        container = Container(settings)
        container.build()
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        
        # Infrastructure
        self.db_manager: Optional[DatabaseManager] = None
        self.candle_repo: Optional[SQLiteCandleRepository] = None
        self.trade_repo: Optional[SQLiteTradeRepository] = None
        
        # Exchange
        self.exchange: Optional[IExchange] = None
        
        # Notifier
        self.notifier: Optional[INotifier] = None
        
        # ML
        self.feature_engine: Optional[CryptoFeatureEngine] = None
        self.model: Optional[CatBoostMLModel] = None
        
        # Domain
        self.portfolio: Optional[Portfolio] = None
        self.risk_engine: Optional[RiskEngine] = None
        
        # Application
        self.position_manager: Optional[PositionManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.signal_handler: Optional[SignalHandler] = None
        self.execution_service: Optional[ExecutionService] = None
        self.trading_engine: Optional[TradingEngine] = None

    def build(self, initial_balance: float = 100.0) -> "Container":
        """Собрать все зависимости."""
        s = self.settings

        # --- Infrastructure ---
        self.db_manager = DatabaseManager(s.DB_PATH)
        self.candle_repo = SQLiteCandleRepository(self.db_manager)
        self.trade_repo = SQLiteTradeRepository(self.db_manager)

        # --- Exchange (по mode) ---
        self.exchange = self._create_exchange(initial_balance)

        # --- Notifier ---
        if s.TG_ENABLED and s.TG_API_KEY and s.TG_CHAT_ID:
            self.notifier = TelegramNotifier(s.TG_API_KEY, s.TG_CHAT_ID)
        else:
            self.notifier = NullNotifier()

        # --- ML ---
        self.feature_engine = CryptoFeatureEngine()
        self.model = CatBoostMLModel(
            confidence_threshold=s.CONFIDENCE_THRESHOLD,
            mfe_threshold=s.MFE_THRESHOLD,
        )

        # --- Domain ---
        self.portfolio = Portfolio(initial_balance)
        self.risk_engine = RiskEngine()

        # --- Application ---
        self.position_manager = PositionManager(self.portfolio, s)
        self.risk_manager = RiskManager(self.portfolio, self.risk_engine, s)
        self.signal_handler = SignalHandler(self.feature_engine, self.model)
        self.execution_service = ExecutionService(
            exchange=self.exchange,
            position_manager=self.position_manager,
            risk_manager=self.risk_manager,
            notifier=self.notifier,
            settings=s,
        )

        # --- Trading Engine (единый flow для live и backtest) ---
        self.trading_engine = TradingEngine(
            signal_handler=self.signal_handler,
            position_manager=self.position_manager,
            execution_service=self.execution_service,
            portfolio=self.portfolio,
            settings=s,
        )

        logger.info(
            f"Container built: mode={s.EXCHANGE_MODE}, "
            f"balance={initial_balance}, leverage={s.LEVERAGE}x"
        )
        return self

    def _create_exchange(self, initial_balance: float) -> IExchange:
        """Фабрика бирж по EXCHANGE_MODE из конфига."""
        mode = self.settings.EXCHANGE_MODE.lower()

        if mode == "binance":
            return BinanceExchange(self.settings)
        elif mode == "bybit":
            return BybitExchange()
        elif mode == "paper":
            binance = BinanceExchange(self.settings)
            return PaperExchange(self.settings, binance, initial_balance=initial_balance)
        elif mode == "simulation":
            return SimulationExchange(self.settings, self.candle_repo)
        else:
            raise ValueError(f"Unknown exchange mode: {mode}. Use: binance, bybit, paper, simulation")

    def load_models(self) -> None:
        """Загрузить ML модели с диска."""
        self.model.load(self.settings.MODELS_DIR)
        logger.info("ML models loaded successfully")