from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .core import Role, current_role, require
from .repositories import InMemoryRepository
from .schemas import (
    AssignmentCreate, AssessmentCreate, AssessmentOut, CaseOut, DoctorCreate, DoctorOut,
    DoctorReviewOut, FacilityCreate, FacilityOut, FollowupCreate, FollowupOut, PatientCreate,
    PatientOut, PatientType, QueueOut, ReferralConfirm, ReferralOut,
    ReferralRecommendationRequest, ReviewDecisionCreate, SpecialQueueCreate, TreatmentCreate,
    TreatmentOut, TriageOut,
)
from .services import HealthcareService

app = FastAPI(title="SIH Healthcare API", version="1.0.0", openapi_url="/api/v1/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
repo = InMemoryRepository()
service = HealthcareService(repo)


@app.get("/health")
def health():
    return {"status": "ok", "storage": "in-memory-development-adapter", "triage_adapter": type(service.triage).__name__}


@app.post("/api/v1/auth/login")
def login(role: Role):
    return {"access_token": f"demo-{role.value}", "token_type": "bearer", "role": role}


@app.get("/api/v1/auth/me")
def me(role: Role = Depends(current_role)):
    return {"role": role}


@app.post("/api/v1/patients", response_model=PatientOut, status_code=201)
def create_patient(data: PatientCreate, _: Role = Depends(require(Role.ASHA, Role.ADMIN))):
    return service.register(data.model_dump())


@app.get("/api/v1/patients", response_model=list[PatientOut])
def list_patients(_: Role = Depends(current_role)):
    return list(repo.patients.values())


@app.get("/api/v1/patients/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: str, _: Role = Depends(current_role)):
    return repo.patient(patient_id)


@app.patch("/api/v1/patients/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: str, data: PatientCreate, _: Role = Depends(require(Role.ASHA, Role.ADMIN))):
    patient = repo.patient(patient_id)
    if patient["patient_type"] != data.patient_type:
        raise HTTPException(400, "patient_type cannot be changed after registration")
    patient.update(data.model_dump())
    return patient


@app.post("/api/v1/patients/{patient_id}/assessment", response_model=AssessmentOut, status_code=201)
def create_assessment(patient_id: str, data: AssessmentCreate, _: Role = Depends(require(Role.ASHA, Role.ADMIN))):
    if repo.patient(patient_id)["patient_type"] != PatientType.NORMAL:
        raise HTTPException(400, "Assessment is only for NORMAL patients")
    return repo.create_assessment(patient_id, data.model_dump())


@app.get("/api/v1/patients/{patient_id}/assessment", response_model=AssessmentOut)
def get_assessment(patient_id: str, _: Role = Depends(current_role)):
    return repo.assessment_for_patient(patient_id)


@app.post("/api/v1/patients/{patient_id}/triage", response_model=TriageOut, status_code=201)
async def triage(patient_id: str, _: Role = Depends(require(Role.ASHA, Role.DOCTOR, Role.ADMIN))):
    try:
        return await service.run_triage(patient_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/patients/{patient_id}/triage", response_model=TriageOut)
def get_triage(patient_id: str, _: Role = Depends(current_role)):
    return repo.triage_for_patient(patient_id)


@app.post("/api/v1/special-queue", response_model=QueueOut, status_code=201)
def create_queue(data: SpecialQueueCreate, _: Role = Depends(require(Role.ASHA, Role.DOCTOR, Role.ADMIN))):
    try:
        return service.create_special_queue(data.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/special-queue", response_model=list[QueueOut])
def list_queue(_: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    return repo.waiting_queue()


@app.get("/api/v1/special-queue/{queue_id}", response_model=QueueOut)
def get_queue(queue_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    return repo.queue_item(queue_id)


@app.post("/api/v1/special-queue/{queue_id}/assign", response_model=QueueOut)
def assign(queue_id: str, data: AssignmentCreate, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    try:
        return service.assign_queue(queue_id, data.doctor_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/special-queue/{queue_id}/start", response_model=QueueOut)
def start(queue_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    row = repo.queue_item(queue_id)
    if row["status"] != "ASSIGNED":
        raise HTTPException(409, "Queue item must be ASSIGNED before starting")
    row["status"] = "IN_PROGRESS"
    return row


@app.post("/api/v1/special-queue/{queue_id}/complete", response_model=QueueOut)
def complete(queue_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    row = repo.queue_item(queue_id)
    if row["status"] != "IN_PROGRESS":
        raise HTTPException(409, "Queue item must be IN_PROGRESS before completing")
    row["status"] = "COMPLETED"
    return row


@app.post("/api/v1/doctors", response_model=DoctorOut, status_code=201)
def create_doctor(data: DoctorCreate, _: Role = Depends(require(Role.ADMIN))):
    return repo.add_doctor(data.model_dump())


@app.get("/api/v1/doctors", response_model=list[DoctorOut])
def list_doctors(_: Role = Depends(current_role)):
    return list(repo.doctors.values())


@app.post("/api/v1/facilities", response_model=FacilityOut, status_code=201)
def create_facility(data: FacilityCreate, _: Role = Depends(require(Role.ADMIN))):
    return repo.add_facility(data.model_dump())


@app.get("/api/v1/facilities", response_model=list[FacilityOut])
def list_facilities(_: Role = Depends(current_role)):
    return list(repo.facilities.values())


@app.get("/api/v1/doctor/reviews")
def pending_reviews(_: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    reviewed = {row["triage_id"] for row in repo.reviews.values()}
    return [row for row in repo.triages.values() if row["id"] not in reviewed]


@app.get("/api/v1/doctor/reviews/{triage_id}")
def review_detail(triage_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    if triage_id not in repo.triages:
        raise HTTPException(404, "Triage result not found")
    triage_row = repo.triages[triage_id]
    return {"patient": repo.patient(triage_row["patient_id"]), "assessment": repo.assessments[triage_row["assessment_id"]], "triage": triage_row}


@app.post("/api/v1/doctor/reviews/{triage_id}/decision", response_model=DoctorReviewOut, status_code=201)
def doctor_decision(triage_id: str, data: ReviewDecisionCreate, doctor_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    try:
        return service.review_normal_case(triage_id, doctor_id, data.decision.value, data.notes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/referrals/recommend")
async def recommend(data: ReferralRecommendationRequest, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    repo.patient(data.patient_id)
    return await service.referral.recommend(specialty=data.specialty, location=data.location, criticality=data.criticality.value)


@app.post("/api/v1/referrals/{review_id}/confirm", response_model=ReferralOut, status_code=201)
def confirm_referral(review_id: str, data: ReferralConfirm, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    review = repo.review(review_id)
    if review["decision"] != "REFERRAL_REQUIRED":
        raise HTTPException(409, "Review is not marked REFERRAL_REQUIRED")
    return service.create_referral(review["patient_id"], data.facility_id, data.doctor_id)


@app.get("/api/v1/referrals/{referral_id}", response_model=ReferralOut)
def get_referral(referral_id: str, _: Role = Depends(current_role)):
    return repo.referral(referral_id)


@app.get("/api/v1/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, _: Role = Depends(current_role)):
    return repo.case(case_id)


@app.post("/api/v1/cases/{case_id}/treatments", response_model=TreatmentOut, status_code=201)
def add_treatment(case_id: str, data: TreatmentCreate, doctor_id: str, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    return service.add_treatment(case_id, doctor_id, data.notes)


@app.post("/api/v1/cases/{case_id}/followups", response_model=FollowupOut, status_code=201)
def add_followup(case_id: str, data: FollowupCreate, _: Role = Depends(require(Role.DOCTOR, Role.ADMIN))):
    return service.add_followup(case_id, data.scheduled_for, data.notes)


@app.get("/api/v1/cases/{case_id}/followups", response_model=list[FollowupOut])
def list_followups(case_id: str, _: Role = Depends(current_role)):
    repo.case(case_id)
    return [item for item in repo.followups.values() if item["case_id"] == case_id]
