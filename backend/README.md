# Legacy FastAPI reference component

> The supported application is the Flask/SQLite implementation in `../sih_complete_architecture`, launched from the repository root with `./run_local.ps1`.

This folder is preserved because it contains useful early API/role design work, but it is **not** an active runtime: it uses an in-memory repository and mock engine clients. It must not be used for the final demo because it cannot persist cases or call the frozen local engines directly.

FastAPI implementation of the workflow in `SIH_Frontend_Backend_Infrastructure_Design.pdf`.

## Run locally

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001
```

Open `http://127.0.0.1:8001/docs` for Swagger UI.

## Temporary development authentication

Use `X-User-Role: ASHA`, `DOCTOR`, or `ADMIN` on requests. `POST /api/v1/auth/login` returns a demo token which the API also accepts as `Authorization: Bearer demo-<ROLE>`.

The current `InMemoryRepository` is deliberately the only temporary layer. Replace it with the database team's Supabase repository behind the same methods; routes, schemas and services should not change. Data resets whenever the API restarts.

## External AI contracts

The Triage adapter becomes live when `TRIAGE_API_URL=http://127.0.0.1:8000` is set before the backend starts. It calls the existing v6 local `/api/triage` endpoint and persists its final criticality and raw response. Without that variable it uses a deterministic mock. The Referral adapter remains a mock until that team provides its final endpoint contract. Do not put ML code in this backend.

## Implemented workflow endpoints

- Patient registration, assessment and triage: `/patients`
- P1/P2/P3 special queue, assignment and state transitions: `/special-queue`
- Doctor and facility management: `/doctors`, `/facilities`
- Doctor review and local/referral decision: `/doctor/reviews`
- Referral recommendation and confirmation: `/referrals`
- Treatment and follow-up: `/cases`
