from datetime import datetime
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PatientType(StrEnum): NORMAL = "NORMAL"; PREGNANT = "PREGNANT"; PRIVATE = "PRIVATE"
class Criticality(StrEnum): GREEN = "GREEN"; YELLOW = "YELLOW"; RED = "RED"
class QueueStatus(StrEnum): WAITING = "WAITING"; ASSIGNED = "ASSIGNED"; IN_PROGRESS = "IN_PROGRESS"; COMPLETED = "COMPLETED"; CANCELLED = "CANCELLED"
class ReviewDecision(StrEnum): LOCAL_TREATMENT = "LOCAL_TREATMENT"; REFERRAL_REQUIRED = "REFERRAL_REQUIRED"


class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=0, le=130)
    gender: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=200)
    patient_type: PatientType


class PatientOut(PatientCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    created_at: datetime


class Vitals(BaseModel):
    temperature: float | None = None
    systolic_bp: int | None = Field(default=None, ge=0, le=300)
    diastolic_bp: int | None = Field(default=None, ge=0, le=250)
    heart_rate: int | None = Field(default=None, ge=0, le=300)
    spo2: float | None = Field(default=None, ge=0, le=100)
    respiratory_rate: int | None = Field(default=None, ge=0, le=80)


class Symptom(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    severity: int = Field(ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)


class AssessmentCreate(BaseModel):
    vitals: Vitals
    symptoms: list[Symptom] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)


class AssessmentOut(AssessmentCreate):
    id: str
    patient_id: str
    created_at: datetime


class TriageOut(BaseModel):
    id: str
    patient_id: str
    assessment_id: str
    criticality: Criticality
    risk_score: float | None
    model_version: str
    raw_response: dict[str, Any]
    created_at: datetime


class SpecialQueueCreate(BaseModel):
    patient_id: str
    patient_type: PatientType
    life_threatening: bool = False
    problem_category: str = Field(min_length=1, max_length=80)


class QueueOut(BaseModel):
    id: str; patient_id: str; patient_type: PatientType; life_threatening: bool
    priority: int; specialty: str; status: QueueStatus; assigned_doctor_id: str | None
    created_at: datetime; assigned_at: datetime | None = None


class AssignmentCreate(BaseModel): doctor_id: str
class ReviewDecisionCreate(BaseModel): decision: ReviewDecision; notes: str | None = Field(default=None, max_length=2000)
class ReferralRecommendationRequest(BaseModel): patient_id: str; specialty: str; criticality: Criticality; location: str | None = None
class ReferralConfirm(BaseModel): facility_id: str; doctor_id: str | None = None
class TreatmentCreate(BaseModel): notes: str = Field(min_length=1, max_length=3000)
class FollowupCreate(BaseModel): scheduled_for: datetime; notes: str | None = Field(default=None, max_length=2000)


class DoctorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    specialty: str = Field(min_length=1, max_length=80)
    experience_years: int = Field(ge=0, le=80)
    location: str | None = Field(default=None, max_length=200)
    active: bool = True
    available: bool = True


class DoctorOut(DoctorCreate):
    id: str
    current_load: int
    created_at: datetime


class FacilityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=200)
    active: bool = True


class FacilityOut(FacilityCreate):
    id: str
    created_at: datetime


class DoctorReviewOut(BaseModel):
    id: str
    patient_id: str
    triage_id: str
    doctor_id: str
    decision: ReviewDecision
    notes: str | None
    created_at: datetime


class ReferralOut(BaseModel):
    id: str
    patient_id: str
    facility_id: str
    doctor_id: str | None
    status: str
    created_at: datetime


class CaseOut(BaseModel):
    id: str
    patient_id: str
    doctor_id: str | None
    source: str
    status: str
    created_at: datetime
    updated_at: datetime


class TreatmentOut(TreatmentCreate):
    id: str
    case_id: str
    doctor_id: str
    created_at: datetime


class FollowupOut(FollowupCreate):
    id: str
    case_id: str
    status: str
    created_at: datetime
