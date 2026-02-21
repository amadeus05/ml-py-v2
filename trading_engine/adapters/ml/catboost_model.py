import logging
import pickle
from pathlib import Path
from typing import Optional, List

import pandas as pd

from trading_engine.interfaces.IModel import IModel
from trading_engine.core.domain.models import Signal
from trading_engine.core.domain.enums import Side

logger = logging.getLogger(__name__)


class CatBoostMLModel(IModel):
    """
    CatBoost ML модель — классификатор + 2 MFE регрессора.
    
    Логика predict 100% как MVP bt.py lines 207-229:
        1. predict_proba → p_short, p_neutral, p_long
        2. if p_long > threshold → signal = 1
        3. elif p_short > threshold → signal = -1
        4. MFE filter: predicted_mfe >= MFE_THRESHOLD
    """

    def __init__(self, confidence_threshold: float, mfe_threshold: float):
        self.confidence_threshold = confidence_threshold
        self.mfe_threshold = mfe_threshold
        self.classifier = None
        self.mfe_long_model = None
        self.mfe_short_model = None
        self.feature_names: List[str] = []
        self._loaded = False

    def load(self, models_dir: str) -> None:
        """Загрузка моделей — как MVP bt.py lines 50-60."""
        from catboost import CatBoostClassifier, CatBoostRegressor

        models_path = Path(models_dir)

        self.classifier = CatBoostClassifier()
        self.classifier.load_model(str(models_path / "catboost_model.cbm"))

        self.mfe_long_model = CatBoostRegressor()
        self.mfe_long_model.load_model(str(models_path / "mfe_long_model.cbm"))

        self.mfe_short_model = CatBoostRegressor()
        self.mfe_short_model.load_model(str(models_path / "mfe_short_model.cbm"))

        with open(models_path / "features.pkl", "rb") as f:
            self.feature_names = pickle.load(f)

        self._loaded = True
        logger.info(
            f"Models loaded from {models_dir}: "
            f"classifier + mfe_long + mfe_short, {len(self.feature_names)} features"
        )

    def predict(self, features: pd.DataFrame) -> Optional[Signal]:
        """
        Генерация сигнала — 100% как MVP bt.py lines 207-229.
        
        Args:
            features: DataFrame с одной строкой, колонки = self.feature_names
        
        Returns:
            Signal или None
        """
        if not self._loaded:
            raise RuntimeError("Models not loaded. Call load() first.")

        current_features = features[self.feature_names]

        # MVP bt.py lines 208-213: predict_proba
        probs = self.classifier.predict_proba(current_features)[0]
        p_short, p_neutral, p_long = 0, 0, 0
        if len(probs) == 2:
            p_short, p_long = probs
        else:
            p_short, p_neutral, p_long = probs

        # MVP bt.py lines 215-219: signal generation
        signal = 0
        confidence = 0.0
        if p_long > self.confidence_threshold:
            signal = 1
            confidence = p_long
        elif p_short > self.confidence_threshold:
            signal = -1
            confidence = p_short

        if signal == 0:
            return None

        # MVP bt.py lines 222-229: MFE filter
        if signal == 1:
            predicted_mfe = self.mfe_long_model.predict(current_features)[0]
        else:
            predicted_mfe = self.mfe_short_model.predict(current_features)[0]

        if predicted_mfe < self.mfe_threshold:
            return None  # Предсказанного движения не хватит до TP

        symbol = features["symbol"].iloc[0] if "symbol" in features.columns else ""

        return Signal(
            symbol=symbol,
            side=Side.from_signal(signal),
            confidence=confidence,
            predicted_mfe=predicted_mfe,
        )

    def get_feature_names(self) -> list:
        return self.feature_names
