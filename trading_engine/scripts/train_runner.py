"""
Train Runner — обучение ML моделей.
100% логика из MVP train.py.
"""
import logging

import pandas as pd
import numpy as np
import pickle
from sklearn.metrics import classification_report, mean_absolute_error

from trading_engine.adapters.config.settings import Settings
from trading_engine.adapters.persistence.database import DatabaseManager
from trading_engine.adapters.persistence.repositories.candle_repository import SQLiteCandleRepository


logger = logging.getLogger(__name__)


def get_purge_gap(settings: Settings) -> pd.Timedelta:
    """Рассчитывает безопасный разрыв — 100% как MVP train.py get_purge_gap()."""
    from trading_engine.core.domain.enums import Timeframe
    bar_hours = Timeframe(settings.TIMEFRAME).hours
    return pd.Timedelta(hours=bar_hours * settings.HORIZON)


def train_model_pair(X_train, y_train, X_test, y_test,
                     y_mfe_long_train, y_mfe_long_test,
                     y_mfe_short_train, y_mfe_short_test,
                     verbose=False):
    """100% как MVP train.py train_model_pair()."""
    from catboost import CatBoostClassifier, CatBoostRegressor

    # 1. КЛАССИФИКАТОР
    clf = CatBoostClassifier(
        iterations=1000, depth=6, learning_rate=0.05,
        loss_function="MultiClass", eval_metric="TotalF1",
        auto_class_weights="Balanced", early_stopping_rounds=200,
        verbose=100 if verbose else 0, allow_writing_files=False,
    )
    clf.fit(X_train, y_train, eval_set=(X_test, y_test), use_best_model=True)
    preds = clf.predict(X_test)
    report = classification_report(y_test, preds, output_dict=True, zero_division=0)

    # 2. MFE LONG
    reg_long = CatBoostRegressor(
        iterations=1000, depth=6, learning_rate=0.05,
        loss_function="RMSE", eval_metric="MAE",
        early_stopping_rounds=200, verbose=100 if verbose else 0,
        allow_writing_files=False,
    )
    reg_long.fit(X_train, y_mfe_long_train, eval_set=(X_test, y_mfe_long_test), use_best_model=True)
    mfe_long_mae = mean_absolute_error(y_mfe_long_test, reg_long.predict(X_test))

    # 3. MFE SHORT
    reg_short = CatBoostRegressor(
        iterations=1000, depth=6, learning_rate=0.05,
        loss_function="RMSE", eval_metric="MAE",
        early_stopping_rounds=200, verbose=100 if verbose else 0,
        allow_writing_files=False,
    )
    reg_short.fit(X_train, y_mfe_short_train, eval_set=(X_test, y_mfe_short_test), use_best_model=True)
    mfe_short_mae = mean_absolute_error(y_mfe_short_test, reg_short.predict(X_test))

    return {
        "clf": clf, "reg_long": reg_long, "reg_short": reg_short,
        "f1_macro": report["macro avg"]["f1-score"],
        "accuracy": report["accuracy"],
        "mfe_long_mae": mfe_long_mae, "mfe_short_mae": mfe_short_mae,
    }


def run_training(settings: Settings = None):
    """100% как MVP train.py train()."""
    if settings is None:
        settings = Settings()

    db = DatabaseManager(settings.DB_PATH)
    candle_repo = SQLiteCandleRepository(db)

    # Load data
    df = candle_repo.load_all_features()
    if df.empty:
        print("❌ No data for training!")
        return

    # Отбрасываем последние HORIZON строк (фейковые метки)
    if len(df) > settings.HORIZON:
        df = df.iloc[:-settings.HORIZON]

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Фичи
    drop_cols = [
        "timestamp", "Target", "MFE_long", "MFE_short",
        "open", "high", "low", "close", "volume",
        "Resistance", "Support", "HTF_EMA_50", "volume_ma_20",
    ]
    features = [c for c in df.columns if c not in drop_cols]

    X = df[features]
    y = df["Target"]
    y_mapped = y.map({-1: 0, 0: 1, 1: 2})
    y_mfe_long = df["MFE_long"]
    y_mfe_short = df["MFE_short"]

    purge_gap = get_purge_gap(settings)
    print(f"Purge gap: {purge_gap}")

    # --- DATA FILTERING ---
    split_time = df["timestamp"].quantile(0.85)

    if not settings.ENABLE_PROD_TRAINING:
        print(f"\n🚫 RESERVING LAST 15% FOR TESTING (STRICT MODE)")
        print(f"  Original end: {df['timestamp'].max()}")
        print(f"  Split time: {split_time}")
        print(f"  Train limit: {split_time - purge_gap}")
        df = df[df["timestamp"] <= (split_time - purge_gap)].reset_index(drop=True)
        print(f"  Training data cut to: {df['timestamp'].max()}")
    else:
        print(f"\n🚀 PRODUCTION MODE: TRAINING ON FULL DATA")
        print(f"  End of data: {df['timestamp'].max()}")

    # Update after filtering
    X = df[features]
    y = df["Target"]
    y_mapped = y.map({-1: 0, 0: 1, 1: 2})
    y_mfe_long = df["MFE_long"]
    y_mfe_short = df["MFE_short"]

    # --- CLASSIC SPLIT ---
    print("\n⚙️ РЕЖИМ CLASSIC (Single Split 85/15)")
    split_time = df["timestamp"].quantile(0.85)
    train_mask = df["timestamp"] <= (split_time - purge_gap)
    test_mask = df["timestamp"] > split_time
    purged_count = len(df) - train_mask.sum() - test_mask.sum()
    print(f"Purge gap: {purge_gap}, removed {purged_count} rows")

    X_train, X_test = X[train_mask], X[test_mask]
    y_train, y_test = y_mapped[train_mask], y_mapped[test_mask]

    res = train_model_pair(
        X_train, y_train, X_test, y_test,
        y_mfe_long[train_mask], y_mfe_long[test_mask],
        y_mfe_short[train_mask], y_mfe_short[test_mask],
        verbose=True,
    )
    model = res["clf"]
    mfe_long_model = res["reg_long"]
    mfe_short_model = res["reg_short"]

    print("\nREPORT (0=Short, 1=Neutral, 2=Long):")
    preds = model.predict(X_test)
    print(classification_report(y_test, preds))

    # --- SAVE ---
    settings.ensure_models_dir()
    models_path = settings.models_path
    model.save_model(str(models_path / "catboost_model.cbm"))
    mfe_long_model.save_model(str(models_path / "mfe_long_model.cbm"))
    mfe_short_model.save_model(str(models_path / "mfe_short_model.cbm"))

    with open(models_path / "features.pkl", "wb") as f:
        pickle.dump(features, f)

    print("\n✅ All models saved!")
    print(f"  - catboost_model.cbm")
    print(f"  - mfe_long_model.cbm")
    print(f"  - mfe_short_model.cbm")
    print(f"  - features.pkl ({len(features)} features)")


if __name__ == "__main__":
    run_training()
