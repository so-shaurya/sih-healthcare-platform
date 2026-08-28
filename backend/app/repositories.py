"""Persistence boundary. Replace InMemoryRepository with a Supabase implementation."""
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from .schemas import QueueStatus


def now() -> datetime: return datetime.now(timezone.utc)
def uid(prefix: str) -> str: return f"{prefix}-{uuid4().hex[:12]}"


class InMemoryRepository:
    def __init__(self) -> None:
        self.patients: dict[str, dict] = {}; self.assessments: dict[str, dict] = {}
        self.triages: dict[str, dict] = {}; self.queue: dict[str, dict] = {}
        self.reviews: dict[str, dict] = {}; self.referrals: dict[str, dict] = {}
        self.cases: dict[str, dict] = {}; self.followups: dict[str, dict] = {}
        self.doctors: dict[str, dict] = {}; self.facilities: dict[str, dict] = {}
        self.treatments: dict[str, dict] = {}; self.recommendations: dict[str, list[dict]] = {}

    def create_patient(self, data: dict) -> dict:
        row = {**data, "id": uid("P"), "status": "REGISTERED", "created_at": now()}; self.patients[row["id"]] = row; return row
    def patient(self, patient_id: str) -> dict:
        if patient_id not in self.patients: raise HTTPException(404, "Patient not found")
        return self.patients[patient_id]
    def create_assessment(self, patient_id: str, data: dict) -> dict:
        self.patient(patient_id); row = {**data, "id": uid("A"), "patient_id": patient_id, "created_at": now()}; self.assessments[row["id"]] = row; return row
    def assessment_for_patient(self, patient_id: str) -> dict:
        rows = [x for x in self.assessments.values() if x["patient_id"] == patient_id]
        if not rows: raise HTTPException(404, "Assessment not found")
        return max(rows, key=lambda x: x["created_at"])
    def add_triage(self, row: dict) -> dict: self.triages[row["id"]] = row; return row
    def triage_for_patient(self, patient_id: str) -> dict:
        rows = [x for x in self.triages.values() if x["patient_id"] == patient_id]
        if not rows: raise HTTPException(404, "Triage result not found")
        return max(rows, key=lambda x: x["created_at"])
    def add_queue(self, row: dict) -> dict: self.queue[row["id"]] = row; return row
    def queue_item(self, queue_id: str) -> dict:
        if queue_id not in self.queue: raise HTTPException(404, "Queue item not found")
        return self.queue[queue_id]
    def waiting_queue(self) -> list[dict]: return sorted((x for x in self.queue.values() if x["status"] == QueueStatus.WAITING), key=lambda x: (x["priority"], x["created_at"]))

    def add_doctor(self, data: dict) -> dict:
        row = {**data, "id": uid("D"), "current_load": 0, "created_at": now()}; self.doctors[row["id"]] = row; return row
    def doctor(self, doctor_id: str) -> dict:
        if doctor_id not in self.doctors: raise HTTPException(404, "Doctor not found")
        return self.doctors[doctor_id]
    def add_facility(self, data: dict) -> dict:
        row = {**data, "id": uid("F"), "created_at": now()}; self.facilities[row["id"]] = row; return row
    def facility(self, facility_id: str) -> dict:
        if facility_id not in self.facilities: raise HTTPException(404, "Facility not found")
        return self.facilities[facility_id]
    def add_review(self, row: dict) -> dict: self.reviews[row["id"]] = row; return row
    def review(self, review_id: str) -> dict:
        if review_id not in self.reviews: raise HTTPException(404, "Doctor review not found")
        return self.reviews[review_id]
    def add_referral(self, row: dict) -> dict: self.referrals[row["id"]] = row; return row
    def referral(self, referral_id: str) -> dict:
        if referral_id not in self.referrals: raise HTTPException(404, "Referral not found")
        return self.referrals[referral_id]
    def add_case(self, row: dict) -> dict: self.cases[row["id"]] = row; return row
    def case(self, case_id: str) -> dict:
        if case_id not in self.cases: raise HTTPException(404, "Case not found")
        return self.cases[case_id]
    def add_treatment(self, row: dict) -> dict: self.treatments[row["id"]] = row; return row
    def add_followup(self, row: dict) -> dict: self.followups[row["id"]] = row; return row
