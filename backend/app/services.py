from .repositories import InMemoryRepository, now, uid
from .schemas import PatientType, QueueStatus
from .integrations import MockReferralClient, triage_client

SPECIALTY_BY_CATEGORY = {"MATERNAL": "GYNAECOLOGY", "CHILD": "PAEDIATRICS", "PRIVATE": "RELEVANT_SPECIALIST"}


class HealthcareService:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo; self.triage = triage_client(); self.referral = MockReferralClient()

    def register(self, data: dict) -> dict:
        patient = self.repo.create_patient(data)
        if patient["patient_type"] != PatientType.NORMAL:
            category = "MATERNAL" if patient["patient_type"] == PatientType.PREGNANT else "PRIVATE"
            self.create_special_queue({"patient_id": patient["id"], "patient_type": patient["patient_type"], "life_threatening": False, "problem_category": category})
        return patient

    def create_special_queue(self, data: dict) -> dict:
        patient = self.repo.patient(data["patient_id"])
        if patient["patient_type"] == PatientType.NORMAL: raise ValueError("NORMAL patients cannot enter special queue")
        if patient["patient_type"] != data["patient_type"]: raise ValueError("patient_type must match registered patient")
        priority = 1 if data["life_threatening"] else (2 if data["patient_type"] == PatientType.PREGNANT else 3)
        category = data["problem_category"].upper()
        specialty = SPECIALTY_BY_CATEGORY.get(category, "RELEVANT_SPECIALIST")
        row = {"id": uid("Q"), **data, "priority": priority, "specialty": specialty, "status": QueueStatus.WAITING, "assigned_doctor_id": None, "created_at": now(), "assigned_at": None}
        return self.repo.add_queue(row)

    async def run_triage(self, patient_id: str) -> dict:
        patient = self.repo.patient(patient_id)
        if patient["patient_type"] != PatientType.NORMAL: raise ValueError("Only NORMAL patients can enter Triage ML")
        assessment = self.repo.assessment_for_patient(patient_id)
        result = await self.triage.predict({**assessment, "patient": patient})
        return self.repo.add_triage({"id": uid("T"), "patient_id": patient_id, "assessment_id": assessment["id"], **result, "created_at": now()})

    def assign_queue(self, queue_id: str, doctor_id: str) -> dict:
        row = self.repo.queue_item(queue_id); doctor = self.repo.doctor(doctor_id)
        if not doctor["active"]: raise ValueError("Cannot assign an inactive doctor")
        if doctor["specialty"] != row["specialty"] and row["specialty"] != "RELEVANT_SPECIALIST": raise ValueError("Doctor specialty does not match queue")
        row.update(assigned_doctor_id=doctor_id, status=QueueStatus.ASSIGNED, assigned_at=now()); doctor["current_load"] += 1
        return row

    def review_normal_case(self, triage_id: str, doctor_id: str, decision: str, notes: str | None) -> dict:
        triage = self.repo.triages.get(triage_id)
        if not triage: raise ValueError("Triage result not found")
        self.repo.doctor(doctor_id)
        review = self.repo.add_review({"id": uid("R"), "patient_id": triage["patient_id"], "triage_id": triage_id, "doctor_id": doctor_id, "decision": decision, "notes": notes, "created_at": now()})
        if decision == "LOCAL_TREATMENT": self.create_case(triage["patient_id"], doctor_id, "LOCAL_TREATMENT")
        return review

    def create_case(self, patient_id: str, doctor_id: str | None, source: str) -> dict:
        return self.repo.add_case({"id": uid("C"), "patient_id": patient_id, "doctor_id": doctor_id, "source": source, "status": "OPEN", "created_at": now(), "updated_at": now()})

    def create_referral(self, patient_id: str, facility_id: str, doctor_id: str | None) -> dict:
        self.repo.patient(patient_id); self.repo.facility(facility_id)
        referral = self.repo.add_referral({"id": uid("REF"), "patient_id": patient_id, "facility_id": facility_id, "doctor_id": doctor_id, "status": "CREATED", "created_at": now()})
        self.create_case(patient_id, doctor_id, "REFERRAL")
        return referral

    def add_treatment(self, case_id: str, doctor_id: str, notes: str) -> dict:
        case = self.repo.case(case_id); self.repo.doctor(doctor_id)
        case.update(status="IN_PROGRESS", updated_at=now())
        return self.repo.add_treatment({"id": uid("TX"), "case_id": case_id, "doctor_id": doctor_id, "notes": notes, "created_at": now()})

    def add_followup(self, case_id: str, scheduled_for, notes: str | None) -> dict:
        self.repo.case(case_id)
        return self.repo.add_followup({"id": uid("FU"), "case_id": case_id, "scheduled_for": scheduled_for, "notes": notes, "status": "SCHEDULED", "created_at": now()})
