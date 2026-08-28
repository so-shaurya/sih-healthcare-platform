"""Black-box adapter interfaces. Replace adapter configuration, not service workflow."""
import os
import httpx
from .schemas import Criticality


class MockTriageClient:
    async def predict(self, assessment: dict) -> dict:
        # Contract ready for the ML team: criticality, risk_score, model_version, raw_response.
        vitals = assessment["vitals"]
        critical = Criticality.RED if (vitals.get("spo2") is not None and vitals["spo2"] < 90) else Criticality.YELLOW
        return {"criticality": critical, "risk_score": 0.5, "model_version": "mock-v1", "raw_response": {"adapter": "mock"}}


class MockReferralClient:
    async def recommend(self, *, specialty: str, location: str | None, criticality: str) -> list[dict]:
        return [{"facility_id": "FAC-DEMO-1", "name": "Demo Community Hospital", "specialty": specialty, "rank": 1, "metadata": {"location": location, "criticality": criticality}}]


class HttpTriageClient:
    """Adapter for the existing local v6 `POST /api/triage` service."""
    def __init__(self, url: str) -> None: self.url = url.rstrip("/")

    async def predict(self, assessment: dict) -> dict:
        v = assessment["vitals"]
        patient = assessment.get("patient", {})
        gender = str(patient.get("gender", "")).upper()
        payload = {"AGE": patient.get("age"), "SEX": 1 if gender in {"F", "FEMALE"} else (2 if gender in {"M", "MALE"} else None), "TEMPF": v.get("temperature"), "BPSYS": v.get("systolic_bp"), "BPDIAS": v.get("diastolic_bp"), "PULSE": v.get("heart_rate"), "POPCT": v.get("spo2"), "RESPR": v.get("respiratory_rate")}
        payload = {key: value for key, value in payload.items() if value is not None}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.url}/api/triage", json=payload)
            response.raise_for_status(); body = response.json()
        return {"criticality": Criticality(body["final_class"]), "risk_score": body.get("stage1_red_probability"), "model_version": "triage-v6", "raw_response": body}


def triage_client():
    url = os.getenv("TRIAGE_API_URL")
    return HttpTriageClient(url) if url else MockTriageClient()
