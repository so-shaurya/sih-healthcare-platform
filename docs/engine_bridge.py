from __future__ import annotations

import csv
import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
# These are the authoritative assets supplied with this project.  Environment
# variables below can still point to an externally managed frozen-asset folder.
ML_DIR = PROJECT_DIR / "ml"
DEFAULT_TRIAGE_DIR = ML_DIR / "SIH healthcare"
DEFAULT_REFERRAL_DIR = ML_DIR / "referral engine"

TRIAGE_ENGINE_PATH = Path(
    os.getenv("TRIAGE_ENGINE_PATH", DEFAULT_TRIAGE_DIR / "second_test.py")
)
TRIAGE_BUNDLE_PATH = Path(
    os.getenv(
        "TRIAGE_BUNDLE_PATH",
        DEFAULT_TRIAGE_DIR / "artifacts" / "triage_safety_pipeline_v6.pkl",
    )
)
REFERRAL_ENGINE_PATH = Path(
    os.getenv("REFERRAL_ENGINE_PATH", DEFAULT_REFERRAL_DIR / "referral_engine.py")
)
REFERRAL_DATA_DIR = Path(
    os.getenv("REFERRAL_DATA_DIR", DEFAULT_REFERRAL_DIR)
)
CONDITION_PIPELINE_PATH = os.getenv("CONDITION_PIPELINE_PATH", "").strip()

_REQUIRED_REFERRAL_FILES = [
    "facilities.csv",
    "facility_capabilities.csv",
    "villages.csv",
    "condition_capability_map.csv",
    "facility_live_status.csv",
    "criticality_ranking_policy.csv",
    "facility_criticality_fit.csv",
]


def _load_module(path: Path, module_name: str) -> ModuleType:
    if not path.exists():
        raise FileNotFoundError(f"Module file not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EngineBridge:
    """Thin integration adapter. The uploaded ML/ranking engines are never edited here."""

    def __init__(self) -> None:
        self._triage_module: ModuleType | None = None
        self._referral_module: ModuleType | None = None
        self._referral_engine: Any | None = None
        self._condition_module: ModuleType | None = None

    def config(self) -> dict[str, Any]:
        referral_missing = [
            name for name in _REQUIRED_REFERRAL_FILES if not (REFERRAL_DATA_DIR / name).exists()
        ]
        model_path = REFERRAL_DATA_DIR / "models" / "load_forecast_model.pkl"
        return {
            "triage": {
                "engine_exists": TRIAGE_ENGINE_PATH.exists(),
                "bundle_exists": TRIAGE_BUNDLE_PATH.exists(),
                "ready": TRIAGE_ENGINE_PATH.exists() and TRIAGE_BUNDLE_PATH.exists(),
            },
            "referral": {
                "engine_exists": REFERRAL_ENGINE_PATH.exists(),
                "missing_data_files": referral_missing,
                "load_forecast_model_exists": model_path.exists(),
                "ready": REFERRAL_ENGINE_PATH.exists() and not referral_missing,
            },
            "condition_pipeline": {
                "mode": "external" if CONDITION_PIPELINE_PATH else "validated_manual_output",
                "configured": bool(CONDITION_PIPELINE_PATH),
                "note": (
                    "When no separate symptom model is configured, the prototype stores symptoms "
                    "separately and accepts a validated suspected_condition as that pipeline's output."
                ),
            },
            "prototype_defaults": {
                "doctor_reviewed": True,
                "referral_required": True,
            },
        }

    def _get_triage_module(self) -> ModuleType:
        if self._triage_module is None:
            self._triage_module = _load_module(TRIAGE_ENGINE_PATH, "sih_triage_engine")
            if not hasattr(self._triage_module, "predict_patient"):
                raise AttributeError("Triage engine must expose predict_patient(patient, bundle_path)")
        return self._triage_module

    def predict_triage(self, triage_payload: dict[str, Any]) -> dict[str, Any]:
        module = self._get_triage_module()
        return module.predict_patient(triage_payload, TRIAGE_BUNDLE_PATH)

    def _get_referral_engine(self):
        if self._referral_engine is None:
            self._referral_module = _load_module(REFERRAL_ENGINE_PATH, "sih_referral_engine")
            cls = getattr(self._referral_module, "ReferralEngine", None)
            if cls is None:
                raise AttributeError("Referral engine must expose ReferralEngine")
            self._referral_engine = cls(REFERRAL_DATA_DIR)
        return self._referral_engine

    def recommend_referral(self, referral_payload: dict[str, Any], top_k: int = 3) -> dict[str, Any]:
        return self._get_referral_engine().recommend(referral_payload, top_k=top_k)

    def villages(self) -> list[dict[str, str]]:
        path = REFERRAL_DATA_DIR / "villages.csv"
        if not path.exists():
            return []
        rows: list[dict[str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                village_id = (row.get("village_id") or "").strip()
                if not village_id:
                    continue
                rows.append({
                    "village_id": village_id,
                    "village_name": (row.get("village_name") or "").strip(),
                    "block": (row.get("block") or "").strip(),
                    "district": (row.get("district") or "").strip(),
                    "state": (row.get("state") or "").strip(),
                })
        return rows

    def condition_choices(self) -> list[str]:
        path = REFERRAL_DATA_DIR / "condition_capability_map.csv"
        if not path.exists():
            return []
        values: list[str] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                value = (row.get("suspected_condition") or "").strip()
                if value and value not in values:
                    values.append(value)
        return values

    def resolve_condition_output(self, symptoms: str, suspected_condition: str | None) -> dict[str, Any]:
        """Return the output of the parallel symptom pipeline.

        If CONDITION_PIPELINE_PATH is configured, it must expose predict_condition(payload)
        and return either a condition string or {"suspected_condition": ...}.
        Otherwise the UI-supplied suspected_condition is treated as the already-computed
        output of that separate pipeline and is validated against the referral map when available.
        """
        symptoms = (symptoms or "").strip()

        if CONDITION_PIPELINE_PATH:
            if self._condition_module is None:
                self._condition_module = _load_module(Path(CONDITION_PIPELINE_PATH), "sih_condition_pipeline")
            fn = getattr(self._condition_module, "predict_condition", None)
            if fn is None:
                raise AttributeError("Condition pipeline must expose predict_condition(payload)")
            raw = fn({"symptoms": symptoms})
            if isinstance(raw, str):
                condition = raw
                extra = {}
            elif isinstance(raw, dict):
                condition = str(raw.get("suspected_condition", "")).strip()
                extra = raw
            else:
                raise TypeError("Condition pipeline output must be a string or dict")
            if not condition:
                raise ValueError("Condition pipeline returned no suspected_condition")
            return {"suspected_condition": condition, "pipeline_output": extra, "mode": "external"}

        condition = (suspected_condition or "").strip()
        if not condition:
            raise ValueError(
                "suspected_condition is required until the separate symptom/condition pipeline is connected"
            )

        choices = self.condition_choices()
        if choices:
            normalized = {" ".join(x.lower().replace("_", " ").split()): x for x in choices}
            key = " ".join(condition.lower().replace("_", " ").split())
            if key not in normalized:
                raise ValueError("suspected_condition is not present in condition_capability_map.csv")
            condition = normalized[key]

        return {
            "suspected_condition": condition,
            "pipeline_output": None,
            "mode": "validated_manual_output",
        }
