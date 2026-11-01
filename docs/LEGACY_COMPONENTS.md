# Preserved source components

The project originally contained three disconnected component sets. They remain in the repository to preserve prior work, but they are not supported runtime entry points.

| Path | What it contains | Why it is not the active application |
| --- | --- | --- |
| `backend/` | FastAPI service with role scaffolding | Uses `InMemoryRepository` and mock triage/referral clients, so it cannot meet persistence or no-fake-ML requirements. |
| `database/` | PostgreSQL/Supabase-oriented schema and a patient route | Incomplete: queue route is absent and it has no complete workflow or engine adapter. |
| `frontend/sahayata-html/` | Polished static workflow mockups | `api.js` intentionally falls back to local mock data when the server is unavailable; it is unsuitable as the production/demo runtime. |

The canonical supported application is `sih_complete_architecture/`. Its Flask API has persistent SQLite storage, engine adapters, and a browser UI; launch it with the root `run_local.ps1`.
