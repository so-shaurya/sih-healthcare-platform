from __future__ import annotations

import argparse
import json
from datetime import datetime
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent


class ReferralEngine:
    """
    Prototype normal-flow healthcare facility referral ranker.

    Scope:
      - Receives triage criticality from the already-built triage engine.
      - Receives a suspected condition selected/entered on the parallel page.
      - Resolves village -> coordinates (or accepts GPS coordinates).
      - Filters facilities by routing capability and safety/availability gates.
      - Forecasts 2-hour facility load.
      - Ranks eligible facilities and returns top options for doctor confirmation.

    It does NOT decide ICU admission, bed assignment, treatment, or internal
    hospital routing.
    """

    def __init__(self, data_dir: str | Path = BASE_DIR):
        self.data_dir = Path(data_dir)

        self.facilities = pd.read_csv(self.data_dir / "facilities.csv")
        self.capabilities = pd.read_csv(self.data_dir / "facility_capabilities.csv")
        self.villages = pd.read_csv(self.data_dir / "villages.csv")
        self.conditions = pd.read_csv(self.data_dir / "condition_capability_map.csv")
        self.live_status = pd.read_csv(self.data_dir / "facility_live_status.csv")
        self.policy = pd.read_csv(self.data_dir / "criticality_ranking_policy.csv")
        self.fit = pd.read_csv(self.data_dir / "facility_criticality_fit.csv")

        self.model_path = self.data_dir / "models" / "load_forecast_model.pkl"
        self.load_model = joblib.load(self.model_path) if self.model_path.exists() else None

        self._normalize_boolean_columns()

    def _normalize_boolean_columns(self) -> None:
        for df, cols in [
            (self.facilities, ["emergency_receiving", "active"]),
            (self.capabilities, ["available"]),
            (self.live_status, ["accepting_referrals"]),
        ]:
            for col in cols:
                if col in df.columns and df[col].dtype == object:
                    df[col] = df[col].astype(str).str.lower().map({"true": True, "false": False})

    @staticmethod
    def _norm(value: Any) -> str:
        return " ".join(str(value).strip().lower().replace("_", " ").split())

    @staticmethod
    def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        earth_radius_km = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        c = 2 * atan2(sqrt(a), sqrt(max(0.0, 1 - a)))
        return earth_radius_km * c

    def _resolve_condition(self, payload: dict[str, Any]) -> dict[str, str]:
        # A future clinical UI may send required_capability directly. The current
        # prototype is expected to send suspected_condition from a dropdown.
        if payload.get("required_capability"):
            capability = self._norm(payload["required_capability"]).replace(" ", "_")
            known = set(self.capabilities.loc[self.capabilities["available"] == True, "capability"])
            if capability not in known:
                raise ValueError(f"Unknown required_capability: {capability}")
            return {
                "suspected_condition": str(payload.get("suspected_condition", "Not supplied")),
                "clinical_category": str(payload.get("clinical_category", "unspecified")),
                "required_capability": capability,
            }

        if not payload.get("suspected_condition"):
            raise ValueError("suspected_condition is required when required_capability is not supplied")

        wanted = self._norm(payload["suspected_condition"])
        temp = self.conditions.copy()
        temp["_norm"] = temp["suspected_condition"].map(self._norm)
        match = temp[temp["_norm"] == wanted]

        if match.empty:
            # Do not silently infer a specialty from uncertain free text. Return
            # close dropdown choices so the UI/user can explicitly select one.
            from difflib import get_close_matches
            choices = temp["_norm"].tolist()
            suggestions_norm = get_close_matches(wanted, choices, n=5, cutoff=0.45)
            suggestions = temp[temp["_norm"].isin(suggestions_norm)]["suspected_condition"].tolist()
            hint = f" Suggested values: {suggestions}" if suggestions else ""
            raise ValueError(
                "Suspected condition is not in condition_capability_map.csv. "
                "Use the UI dropdown or add a validated mapping." + hint
            )

        row = match.iloc[0]
        return {
            "suspected_condition": str(row["suspected_condition"]),
            "clinical_category": str(row["clinical_category"]),
            "required_capability": str(row["required_capability"]),
        }

    def _resolve_location(self, payload: dict[str, Any]) -> dict[str, Any]:
        loc = payload.get("patient_location") or {}
        if not isinstance(loc, dict):
            raise ValueError("patient_location must be a JSON object")

        # GPS/direct coordinates override village-centre coordinates when supplied.
        if loc.get("latitude") is not None and loc.get("longitude") is not None:
            return {
                "source": "gps_or_direct_coordinates",
                "village_id": loc.get("village_id"),
                "village_name": loc.get("village_name"),
                "block": loc.get("block"),
                "district": loc.get("district"),
                "state": loc.get("state"),
                "latitude": float(loc["latitude"]),
                "longitude": float(loc["longitude"]),
            }

        if loc.get("village_id"):
            match = self.villages[self.villages["village_id"] == str(loc["village_id"]).strip()]
        elif loc.get("village_name"):
            name = self._norm(loc["village_name"])
            match = self.villages[self.villages["village_name"].map(self._norm) == name]
            if loc.get("district"):
                match = match[match["district"].map(self._norm) == self._norm(loc["district"])]
        else:
            raise ValueError("Provide village_id, village_name, or latitude/longitude in patient_location")

        if match.empty:
            raise ValueError("Village not found in villages.csv")
        if len(match) > 1:
            raise ValueError("Village name is ambiguous; send village_id or include district")

        row = match.iloc[0]
        return {
            "source": "offline_village_lookup",
            "village_id": str(row["village_id"]),
            "village_name": str(row["village_name"]),
            "block": str(row["block"]),
            "district": str(row["district"]),
            "state": str(row["state"]),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
        }

    def _parse_arrival(self, payload: dict[str, Any]) -> datetime:
        raw = payload.get("arrival_datetime")
        if not raw:
            return datetime.now()
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as exc:
            raise ValueError("arrival_datetime must be ISO-8601, e.g. 2026-08-26T14:00:00") from exc

    def _get_policy(self, criticality: str) -> dict[str, Any]:
        row = self.policy[self.policy["criticality"] == criticality]
        if row.empty:
            raise ValueError(f"No ranking policy configured for {criticality}")
        r = row.iloc[0]
        return {
            "minimum_facility_level": int(r["minimum_facility_level"]),
            "require_emergency_receiving": bool(r["require_emergency_receiving"]),
            "max_load_gate_pct": float(r["max_load_gate_pct"]),
            "distance_weight": float(r["distance_weight"]),
            "facility_fit_weight": float(r["facility_fit_weight"]),
            "current_load_weight": float(r["current_load_weight"]),
            "forecast_load_weight": float(r["forecast_load_weight"]),
            "capacity_weight": float(r["capacity_weight"]),
        }

    def _base_candidates(self, capability: str, location: dict[str, Any], arrival: datetime) -> pd.DataFrame:
        candidates = self.facilities.copy()
        candidates = candidates.merge(self.live_status, on="facility_id", how="left")

        cap_ids = self.capabilities[
            (self.capabilities["capability"] == capability)
            & (self.capabilities["available"] == True)
        ][["facility_id"]].drop_duplicates()
        candidates = candidates.merge(cap_ids.assign(capability_match=True), on="facility_id", how="left")
        candidates["capability_match"] = candidates["capability_match"].eq(True)

        candidates["distance_km"] = candidates.apply(
            lambda r: self.haversine_km(
                location["latitude"], location["longitude"], float(r["latitude"]), float(r["longitude"])
            ), axis=1
        )

        # Prepare model features for each candidate.
        candidates["hour"] = arrival.hour
        candidates["day_of_week"] = arrival.weekday()
        candidates["is_weekend"] = int(arrival.weekday() >= 5)

        feature_cols = [
            "facility_id", "facility_level", "hour", "day_of_week", "is_weekend",
            "baseline_load_pct", "current_load_pct", "referrals_last_hour", "rolling_3h_load_pct"
        ]

        if self.load_model is not None:
            pred = self.load_model.predict(candidates[feature_cols])
            candidates["predicted_load_2h_pct"] = pd.Series(pred).clip(0, 100)
            candidates["forecast_source"] = "hist_gradient_boosting_model"
        else:
            # Conservative fallback: use recent 3-hour load and current load.
            candidates["predicted_load_2h_pct"] = (
                0.65 * candidates["current_load_pct"] + 0.35 * candidates["rolling_3h_load_pct"]
            ).clip(0, 100)
            candidates["forecast_source"] = "fallback_recent_load_blend"

        return candidates

    def _apply_hard_filters(self, candidates: pd.DataFrame, criticality: str, policy: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, int]]:
        reason_counts = {
            "inactive": int((candidates["active"] != True).sum()),
            "not_accepting_referrals": int((candidates["accepting_referrals"] != True).sum()),
            "capability_mismatch": int((candidates["capability_match"] != True).sum()),
            "below_minimum_facility_level": int((candidates["facility_level"] < policy["minimum_facility_level"]).sum()),
            "above_load_gate": int((candidates["current_load_pct"] >= policy["max_load_gate_pct"]).sum()),
            "no_referral_capacity": int((candidates["available_referral_slots"] <= 0).sum()),
        }
        if policy["require_emergency_receiving"]:
            reason_counts["not_emergency_receiving"] = int((candidates["emergency_receiving"] != True).sum())

        mask = (
            (candidates["active"] == True)
            & (candidates["accepting_referrals"] == True)
            & (candidates["capability_match"] == True)
            & (candidates["facility_level"] >= policy["minimum_facility_level"])
            & (candidates["current_load_pct"] < policy["max_load_gate_pct"])
            & (candidates["available_referral_slots"] > 0)
        )
        if policy["require_emergency_receiving"]:
            mask &= candidates["emergency_receiving"] == True

        return candidates[mask].copy(), reason_counts

    def _rank(self, eligible: pd.DataFrame, criticality: str, policy: dict[str, Any]) -> pd.DataFrame:
        fit = self.fit[self.fit["criticality"] == criticality][["facility_level", "fit_score"]]
        ranked = eligible.merge(fit, on="facility_level", how="left")

        ranked["distance_score"] = 1 / (1 + ranked["distance_km"] / 15.0)
        ranked["current_load_score"] = (1 - ranked["current_load_pct"] / 100.0).clip(0, 1)
        ranked["forecast_load_score"] = (1 - ranked["predicted_load_2h_pct"] / 100.0).clip(0, 1)
        ranked["capacity_score"] = (
            ranked["available_referral_slots"] / ranked["max_referral_capacity_per_hour"]
        ).clip(0, 1)

        ranked["referral_score"] = (
            policy["distance_weight"] * ranked["distance_score"]
            + policy["facility_fit_weight"] * ranked["fit_score"]
            + policy["current_load_weight"] * ranked["current_load_score"]
            + policy["forecast_load_weight"] * ranked["forecast_load_score"]
            + policy["capacity_weight"] * ranked["capacity_score"]
        )

        return ranked.sort_values(["referral_score", "distance_km"], ascending=[False, True]).reset_index(drop=True)

    @staticmethod
    def _row_to_result(row: pd.Series, rank: int, criticality: str, capability: str) -> dict[str, Any]:
        reasons = [
            f"Provides required routing capability: {capability}",
            f"Approximately {float(row['distance_km']):.1f} km from the patient location",
            f"Facility level {int(row['facility_level'])} is suitable for {criticality} routing",
            f"Current synthetic load: {float(row['current_load_pct']):.1f}%",
            f"Predicted 2-hour synthetic load: {float(row['predicted_load_2h_pct']):.1f}%",
            f"Referral intake slots available: {int(row['available_referral_slots'])}",
        ]
        if criticality == "RED":
            reasons.insert(1, "Facility is marked as emergency-receiving")

        return {
            "rank": rank,
            "facility_id": str(row["facility_id"]),
            "facility_name": str(row["facility_name"]),
            "facility_type": str(row["facility_type"]),
            "facility_level": int(row["facility_level"]),
            "district": str(row["district"]),
            "distance_km": round(float(row["distance_km"]), 2),
            "required_capability": capability,
            "current_load_pct": round(float(row["current_load_pct"]), 1),
            "predicted_load_2h_pct": round(float(row["predicted_load_2h_pct"]), 1),
            "available_referral_slots": int(row["available_referral_slots"]),
            "referral_score": round(float(row["referral_score"]), 4),
            "forecast_source": str(row["forecast_source"]),
            "why_ranked": reasons,
        }

    def recommend(self, payload: dict[str, Any], top_k: int = 3) -> dict[str, Any]:
        flow = str(payload.get("patient_flow", "NORMAL")).upper().strip()
        if flow == "SPECIAL":
            return {
                "status": "special_case_bypass",
                "message": "Special-case patients follow the priority queue/direct specialist path and do not use the normal referral engine.",
                "next_step": "specialized_priority_queue",
                "prototype_only": True,
            }
        if flow != "NORMAL":
            raise ValueError("patient_flow must be NORMAL or SPECIAL")

        if not bool(payload.get("doctor_reviewed", False)):
            return {
                "status": "not_ready_for_referral_engine",
                "message": "Doctor review must occur before the referral-required decision in the normal workflow.",
                "next_step": "doctor_review",
                "prototype_only": True,
            }

        if not bool(payload.get("referral_required", False)):
            return {
                "status": "referral_not_required",
                "message": "Doctor marked referral as not required.",
                "next_step": "local_treatment_and_care",
                "prototype_only": True,
            }

        criticality = str(payload.get("criticality", "")).upper().strip()
        if criticality not in {"RED", "YELLOW", "GREEN"}:
            raise ValueError("criticality must be RED, YELLOW, or GREEN")

        condition = self._resolve_condition(payload)
        location = self._resolve_location(payload)
        arrival = self._parse_arrival(payload)
        policy = self._get_policy(criticality)

        candidates = self._base_candidates(condition["required_capability"], location, arrival)
        eligible, filter_summary = self._apply_hard_filters(candidates, criticality, policy)

        if eligible.empty:
            nearest_cap_matches = candidates[candidates["capability_match"] == True].sort_values("distance_km").head(3)
            manual_options = [
                {
                    "facility_id": str(r["facility_id"]),
                    "facility_name": str(r["facility_name"]),
                    "distance_km": round(float(r["distance_km"]), 2),
                    "current_load_pct": round(float(r["current_load_pct"]), 1),
                }
                for _, r in nearest_cap_matches.iterrows()
            ]
            return {
                "status": "no_eligible_facility",
                "patient_id": payload.get("patient_id", "UNKNOWN"),
                "criticality": criticality,
                **condition,
                "patient_location": location,
                "message": "No facility passed all configured prototype eligibility filters. Do not auto-route; escalate for manual coordination.",
                "manual_review_candidates": manual_options,
                "filter_summary": filter_summary,
                "next_step": "manual_referral_coordination",
                "prototype_only": True,
            }

        ranked = self._rank(eligible, criticality, policy)
        top_k = max(1, min(int(top_k), len(ranked)))
        recommendations = [
            self._row_to_result(row, i + 1, criticality, condition["required_capability"])
            for i, (_, row) in enumerate(ranked.head(top_k).iterrows())
        ]

        return {
            "status": "pending_doctor_confirmation",
            "patient_id": payload.get("patient_id", "UNKNOWN"),
            "patient_flow": "NORMAL",
            "criticality": criticality,
            **condition,
            "patient_location": location,
            "arrival_datetime": arrival.isoformat(),
            "eligible_facility_count": int(len(ranked)),
            "recommended_facility": recommendations[0],
            "alternatives": recommendations[1:],
            "algorithm": "hard eligibility filters + criticality-aware weighted MCDA; Histogram Gradient Boosting for 2-hour load forecast",
            "next_step": "doctor_confirms_referral",
            "prototype_only": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Final synthetic normal-flow referral engine prototype")
    parser.add_argument("--input", required=True, help="Path to referral input JSON")
    parser.add_argument("--top-k", type=int, default=3, help="Number of facilities to return")
    parser.add_argument("--data-dir", default=str(BASE_DIR), help="Dataset directory")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)

    engine = ReferralEngine(args.data_dir)
    result = engine.recommend(payload, top_k=args.top_k)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
