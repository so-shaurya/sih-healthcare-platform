# SIH Complete Integrated Prototype

This is the runnable full-stack SIH healthcare prototype. It uses a Flask API, a persistent SQLite application database, and a single responsive web interface for the ASHA and doctor workflow. The triage and referral engines are frozen: the application only imports them through `engine_bridge.py` adapters.

This package connects the exact uploaded triage and referral Python engines through a Flask backend, SQLite encounter database, and one ASHA-facing web UI.

## Frozen engine files included

- `engines/triage/triage_engine.py` — copy of uploaded `second_test.py`
- `engines/referral/referral_engine.py` — copy of uploaded referral engine
- `engines/referral/train_load_forecast.py` — copy of uploaded load-model training script

The integration code does **not** edit their prediction/ranking logic.

## Connected ML and referral assets

The supplied authoritative assets are now loaded from the repository's `../ml/` folder by default. The adapter uses:

### Triage

`../ml/SIH healthcare/artifacts/triage_safety_pipeline_v6.pkl`

### Referral

`../ml/referral engine/` contains:

- `facilities.csv`
- `facility_capabilities.csv`
- `villages.csv`
- `condition_capability_map.csv`
- `facility_live_status.csv`
- `criticality_ranking_policy.csv`
- `facility_criticality_fit.csv`
- `models/load_forecast_model.pkl` (present and loaded)

## Override asset locations

Set environment variables only when using another approved frozen-asset location:

```powershell
$env:TRIAGE_BUNDLE_PATH = 'D:\secure-assets\triage_safety_pipeline_v6.pkl'
$env:REFERRAL_DATA_DIR = 'D:\secure-assets\referral-data'
```

See `.env.example` for the complete list. Relative paths resolve from this project folder.

## Install and run

```bat
cd "C:\path\to\sih_complete_architecture"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

`http://127.0.0.1:5000`

Check integration readiness:

`http://127.0.0.1:5000/api/config`

On Windows, the simplest route is:

```powershell
cd 'C:\path\to\SIH app\sih_complete_architecture'
.\run_local.ps1
```

The first run creates `data/prototype.db`. It persists patients, encounters, separate triage and condition results, explicit doctor reviews, referrals, special cases, treatment notes, and follow-ups. The app uses the supplied local frozen assets by default and reports unavailable assets rather than silently producing a fake prediction or referral if an override is invalid.

## Normal flow

1. ASHA registers patient.
2. ASHA enters the existing triage inputs.
3. Backend calls the frozen triage engine and stores `final_class` as criticality.
4. ASHA enters symptoms separately; the condition-pipeline output is stored separately.
5. Backend merger fetches both outputs + registration data.
6. Prototype inserts `doctor_reviewed=true` and `referral_required=true`.
7. Backend calls frozen referral engine.
8. Web shows ranked facilities.
9. Doctor confirms a facility.
10. Follow-up is recorded.

## Condition pipeline adapter

You have not uploaded the separate symptom/condition model yet. For now `/api/condition` stores symptoms and requires a suspected condition selected from `condition_capability_map.csv`. This value is treated as the output of that independent pipeline.

When that pipeline is ready, set `CONDITION_PIPELINE_PATH` to a Python file exposing:

```python
def predict_condition(payload):
    # payload == {"symptoms": "..."}
    return {"suspected_condition": "Fracture"}
```

No triage/referral engine changes are needed.

## Special flow

Special patients bypass the normal referral engine:

- P1: pregnant + disease
- P2: pregnant only
- P3: private/sensitive disease, male or female

Queue order is `(priority ASC, arrival_datetime ASC)`.

## Database

SQLite is created automatically at `data/prototype.db`.

Delete that file if you want a fresh demo database.

## API overview

- `GET /health`, `GET /api/config`, `GET /api/dashboard`
- `POST /api/register`, `GET /api/patients`, `GET /api/patients/<patient_id>`
- `POST /api/triage/<encounter_id>`, `POST /api/condition/<encounter_id>`
- `GET /api/doctor-reviews`, `POST /api/doctor-review/<encounter_id>`
- `POST /api/referral/<encounter_id>`, `GET /api/referrals`, `GET /api/referral/<encounter_id>`, `POST /api/referral/<encounter_id>/confirm`
- `POST /api/special/<encounter_id>`, `GET /api/special-queue`
- `POST /api/treatment/<encounter_id>`, `POST /api/followup/<encounter_id>`, `GET /api/followups`
- `GET /api/encounters`, `GET /api/encounter/<encounter_id>`

## Test

After installing requirements, run the offline special-flow smoke test:

```powershell
python -m unittest discover -s tests -v
```

It confirms real normal-flow triage/referral integration, persistent special-case classification, priority-first / arrival-second ordering, and unified follow-up recording.

See `ARCHITECTURE.md` for the complete data flow.
