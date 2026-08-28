# SIH Healthcare Platform

This repository has one supported local application: [`sih_complete_architecture`](sih_complete_architecture). It is a Flask + SQLite healthcare workflow prototype with a browser UI, persistent data, explicit adapters around the frozen triage/referral engines, special-case routing, doctor review, referrals, treatment, and follow-up.

## Start the application

Install Python 3.11+ once, then run from this repository root:

```powershell
.\run_local.ps1
```

Open `http://127.0.0.1:5000`. The local SQLite database is created at `sih_complete_architecture/data/prototype.db` and is ignored by Git.

## Architecture

```text
Browser UI → Flask API → SQLite persistence → integration adapters → frozen engines
                                            ↘ special priority queue / follow-up
```

- Triage input and symptoms remain separate; they only meet in the backend referral merger.
- Special P1/P2/P3 cases never call the normal referral ranking engine.
- The normal referral engine receives only the database-built payload, never a frontend payload.
- The supplied frozen assets under `ml/` are loaded through adapters; an invalid environment override returns a visible controlled unavailable state rather than fake ML/ranking output.

## Frozen ML components

The authoritative frozen assets are in `ml/SIH healthcare/` and `ml/referral engine/`. Their prediction/ranking logic and model assets must not be edited. [`ML_ASSET_HASHES.txt`](ML_ASSET_HASHES.txt) records an integration-time SHA-256 baseline; the protected source copies retain their recorded hashes in `sih_complete_architecture/ENGINE_SOURCE_HASHES.txt`.

## ML asset configuration

The supplied assets are already auto-configured. Use [`sih_complete_architecture/.env.example`](sih_complete_architecture/.env.example) only to override locations. The current local assets include:

- `triage_safety_pipeline_v6.pkl`
- referral CSV files: facilities, capabilities, villages, condition mapping, live status, policy, and fit
- trained load forecast `.pkl`; no separate condition/symptom model was supplied, so the transparent validated-manual condition adapter remains active

See [`sih_complete_architecture/README.md`](sih_complete_architecture/README.md) for exact filenames, API details, workflows, and tests.

## Repository status

The `backend/`, `database/`, and `frontend/sahayata-html/` folders are preserved source/reference components from the original submission. They are not launched by the root script because they contain an in-memory/mock backend, incomplete PostgreSQL starter, and mock-fallback frontend respectively. They are documented in [`LEGACY_COMPONENTS.md`](LEGACY_COMPONENTS.md), leaving one supported application rather than competing runtimes.
