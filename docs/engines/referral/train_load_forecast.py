from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "facility_load_history.csv"
MODEL_DIR = BASE_DIR / "models"
MODEL_FILE = MODEL_DIR / "load_forecast_model.pkl"
METRICS_FILE = MODEL_DIR / "load_forecast_metrics.json"

CATEGORICAL_FEATURES = ["facility_id"]
NUMERIC_FEATURES = [
    "facility_level",
    "hour",
    "day_of_week",
    "is_weekend",
    "baseline_load_pct",
    "current_load_pct",
    "referrals_last_hour",
    "rolling_3h_load_pct",
]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
TARGET = "load_2h_ahead_pct"


def train() -> dict:
    df = pd.read_csv(DATA_FILE, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Temporal holdout rather than a random split: recent hours remain unseen.
    unique_times = sorted(df["timestamp"].unique())
    split_idx = int(len(unique_times) * 0.80)
    cutoff = unique_times[split_idx]
    train_df = df[df["timestamp"] < cutoff].copy()
    test_df = df[df["timestamp"] >= cutoff].copy()

    preprocess = ColumnTransformer(
        transformers=[
            ("facility", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ],
        remainder="drop",
    )

    # Strong, compact tabular model for the prototype. It captures nonlinear
    # time/load interactions without requiring feature scaling.
    model = HistGradientBoostingRegressor(
        max_iter=220,
        learning_rate=0.07,
        max_leaf_nodes=31,
        l2_regularization=0.5,
        random_state=42,
    )

    pipeline = Pipeline([
        ("preprocess", preprocess),
        ("model", model),
    ])

    pipeline.fit(train_df[FEATURES], train_df[TARGET])
    pred = pipeline.predict(test_df[FEATURES])

    metrics = {
        "algorithm": "HistGradientBoostingRegressor",
        "target": TARGET,
        "forecast_horizon_hours": 2,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "temporal_cutoff": str(pd.Timestamp(cutoff)),
        "mae": round(float(mean_absolute_error(test_df[TARGET], pred)), 4),
        "rmse": round(float(mean_squared_error(test_df[TARGET], pred) ** 0.5), 4),
        "r2": round(float(r2_score(test_df[TARGET], pred)), 4),
        "features": FEATURES,
        "synthetic_data_only": True,
    }

    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(pipeline, MODEL_FILE)
    METRICS_FILE.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, indent=2))
    print(f"Saved model: {MODEL_FILE}")
