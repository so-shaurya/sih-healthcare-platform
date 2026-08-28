# Final Synthetic Referral Engine Prototype

## Scope

This package implements the **normal-patient referral branch** in the supplied workflow:

`ASHA registration -> vitals/symptoms -> triage ML -> RED/YELLOW/GREEN -> doctor review -> referral required? -> referral engine -> doctor confirmation`

Special-case patients (priority queue / direct specialist routing) intentionally **bypass this normal referral engine**, matching the workflow diagram.

All data in this package is synthetic and is for prototype/testing use only. It must not be used for real patient routing or emergency decisions.

## Referral-engine input contract

The engine expects the triage output plus the suspected-condition page and village selection:

```json
{
  "patient_id": "PAT001",
  "patient_flow": "NORMAL",
  "doctor_reviewed": true,
  "referral_required": true,
  "criticality": "RED",
  "suspected_condition": "Suspected heart attack",
  "patient_location": {
    "village_id": "VIL001"
  },
  "arrival_datetime": "2026-08-26T14:00:00"
}
```

The UI should preferably provide `village_id` from a dropdown. The engine looks up the village latitude/longitude internally. Direct GPS coordinates are also supported as an override.

## Decision pipeline

1. Validate that this is the normal-patient flow and that a doctor marked referral as required.
2. Map the selected suspected condition to a routing capability (`cardiology`, `nephrology`, etc.).
3. Resolve the patient's village to latitude/longitude.
4. Load candidate facilities and their routing capabilities.
5. Apply hard eligibility filters: active, accepting referrals, capability match, criticality/facility-level rules, emergency-receiving rule for RED.
6. Calculate Haversine distance from patient/village to each eligible facility.
7. Predict each candidate's 2-hour load using the trained load-forecast model (or use a safe fallback if the model file is absent).
8. Rank candidates with criticality-aware weighted MCDA using distance, facility fit, current load, forecast load, and referral-intake capacity.
9. Return the top 3 and mark the result as `pending_doctor_confirmation`.

## Algorithms

### Final facility choice: hard filters + weighted MCDA

This is the recommended v1 algorithm. Capability and safety constraints should not be learned from synthetic labels; they are explicit gates. A transparent weighted ranker is then used among eligible facilities.

### Load forecast: Histogram Gradient Boosting regression

`train_load_forecast.py` trains a `HistGradientBoostingRegressor` on `facility_load_history.csv` to predict load two hours ahead. This is the genuinely predictive ML component in the prototype.

When real referral-outcome data exists later, a learning-to-rank model such as LambdaMART/XGBoost Ranker can replace the weighted ranker.

## Dataset files

- `facilities.csv` — 29 synthetic healthcare facilities and routing-level metadata.
- `facility_capabilities.csv` — facility-to-clinical-capability mapping.
- `villages.csv` — 180 synthetic villages with coordinates for offline location lookup.
- `condition_capability_map.csv` — suspected-condition dropdown to routing capability mapping.
- `facility_live_status.csv` — current synthetic load, referral intake availability, and queue snapshot.
- `facility_load_history.csv` — multi-day synthetic history used to train the 2-hour load model.
- `criticality_ranking_policy.csv` — RED/YELLOW/GREEN hard gates and ranking weights.
- `facility_criticality_fit.csv` — facility-level suitability scores by criticality.
- `special_case_policy.csv` — documents the special-case bypass path from the workflow.
- `sample_referral_cases.csv` — 600 synthetic end-to-end referral requests for testing.
- `sample_referral_input.json` — one ready-to-run request.

## Code files

- `referral_engine.py` — final normal-flow referral engine.
- `train_load_forecast.py` — trains and saves `models/load_forecast_model.pkl`.
- `api.py` — optional Flask API wrapper.
- `requirements.txt` — Python dependencies.
- `models/load_forecast_model.pkl` — trained synthetic 2-hour load forecast model.
- `models/load_forecast_metrics.json` — temporal holdout evaluation metrics.
- `sample_referral_output.json` — example ranked output ending at doctor confirmation.

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Train/retrain the forecast model:

```bash
python train_load_forecast.py
```

Test the referral engine:

```bash
python referral_engine.py --input sample_referral_input.json
```

Optional Flask API:

```bash
python api.py
```

Then POST JSON to `/api/referral/recommend`.

## Important system boundary

This module selects an appropriate **facility**. It does not decide ICU admission, bed assignment, treatment, surgery, or internal hospital routing. Those decisions remain with the receiving hospital/clinical team.
