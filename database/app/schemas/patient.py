from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime

class PatientCreate(BaseModel):
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    location: str
    patient_type: str  # "NORMAL", "PREGNANT", or "PRIVATE"
    life_threatening: Optional[bool] = False

class PatientResponse(BaseModel):
    id: UUID
    name: str
    patient_type: str
    location: str
    created_at: datetime

    class Config:
        from_attributes = True