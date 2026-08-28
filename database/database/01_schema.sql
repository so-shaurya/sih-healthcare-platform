-- Enums
CREATE TYPE user_role AS ENUM ('ASHA', 'DOCTOR', 'ADMIN');
CREATE TYPE patient_type AS ENUM ('NORMAL', 'PREGNANT', 'PRIVATE');
CREATE TYPE criticality_level AS ENUM ('GREEN', 'YELLOW', 'RED');
CREATE TYPE review_decision AS ENUM ('LOCAL_TREATMENT', 'REFERRAL_REQUIRED');
CREATE TYPE case_status AS ENUM ('OPEN', 'IN_TREATMENT', 'FOLLOW_UP', 'CLOSED');

-- Core Users & Profiles
CREATE TABLE profiles (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    role user_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE specialties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE doctors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    specialty_id UUID REFERENCES specialties(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    current_load INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Patients & Assessments
CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    age INT,
    gender TEXT,
    phone TEXT,
    location TEXT NOT NULL,
    patient_type patient_type NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE vitals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    temperature NUMERIC,
    systolic_bp INT,
    diastolic_bp INT,
    heart_rate INT,
    spo2 NUMERIC,
    respiratory_rate INT
);

CREATE TABLE symptoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    notes TEXT
);

-- ML Triage & Reviews
CREATE TABLE triage_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    assessment_id UUID REFERENCES assessments(id) ON DELETE SET NULL,
    criticality criticality_level NOT NULL,
    risk_score NUMERIC,
    raw_response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);