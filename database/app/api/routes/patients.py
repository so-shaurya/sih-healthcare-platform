from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.schemas.patient import PatientCreate, PatientResponse

router = APIRouter(prefix="/api/v1/patients", tags=["Patients"])

@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def register_patient(payload: PatientCreate, db: AsyncSession = Depends(get_db)):
    insert_patient_query = text("""
        INSERT INTO patients (name, age, gender, phone, location, patient_type)
        VALUES (:name, :age, :gender, :phone, :location, CAST(:patient_type AS patient_type))
        RETURNING id, name, patient_type, location, created_at;
    """)
    
    result = await db.execute(insert_patient_query, {
        "name": payload.name,
        "age": payload.age,
        "gender": payload.gender,
        "phone": payload.phone,
        "location": payload.location,
        "patient_type": payload.patient_type.upper()
    })
    new_patient = result.mappings().one()

    await db.commit()
    return new_patient