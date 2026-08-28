from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SIH_DB_PATH", BASE_DIR / "data" / "prototype.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS encounters (
    encounter_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    patient_flow TEXT NOT NULL CHECK(patient_flow IN ('NORMAL','SPECIAL')),
    sex TEXT,
    village_id TEXT,
    arrival_datetime TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'REGISTERED',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    full_name TEXT,
    phone TEXT,
    age INTEGER CHECK(age IS NULL OR age BETWEEN 0 AND 130),
    sex TEXT CHECK(sex IS NULL OR sex IN ('FEMALE','MALE','OTHER')),
    village_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_results (
    encounter_id TEXT PRIMARY KEY,
    criticality TEXT NOT NULL CHECK(criticality IN ('RED','YELLOW','GREEN')),
    input_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS condition_results (
    encounter_id TEXT PRIMARY KEY,
    symptoms TEXT NOT NULL,
    suspected_condition TEXT NOT NULL,
    pipeline_mode TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS referrals (
    encounter_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    selected_facility_id TEXT,
    selected_facility_name TEXT,
    status TEXT NOT NULL DEFAULT 'RANKED',
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS special_cases (
    encounter_id TEXT PRIMARY KEY,
    is_pregnant INTEGER NOT NULL DEFAULT 0,
    has_other_disease INTEGER NOT NULL DEFAULT 0,
    private_disease INTEGER NOT NULL DEFAULT 0,
    disease_details TEXT,
    private_disease_details TEXT,
    priority INTEGER NOT NULL CHECK(priority IN (1,2,3)),
    priority_label TEXT NOT NULL,
    specialist_route TEXT NOT NULL,
    queue_status TEXT NOT NULL DEFAULT 'WAITING',
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS followups (
    followup_id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id TEXT NOT NULL,
    outcome TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doctor_reviews (
    encounter_id TEXT PRIMARY KEY,
    reviewed INTEGER NOT NULL CHECK(reviewed IN (0,1)),
    referral_required INTEGER NOT NULL CHECK(referral_required IN (0,1)),
    reviewer_name TEXT,
    notes TEXT,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS treatments (
    treatment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id TEXT NOT NULL,
    clinician_name TEXT,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(encounter_id) REFERENCES encounters(encounter_id) ON DELETE CASCADE
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # The first prototype used encounters as the only patient store.  Keep existing
        # encounter rows intact while adding a proper reusable patient record.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(encounters)")}
        if "patient_name" not in columns:
            conn.execute("ALTER TABLE encounters ADD COLUMN patient_name TEXT")


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
