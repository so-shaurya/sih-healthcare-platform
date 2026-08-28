from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from flask import Flask, jsonify, render_template, request

from db import connect, init_db, json_text, now_iso, row_dict
from engine_bridge import EngineBridge

app = Flask(__name__)
bridge = EngineBridge()
init_db()

# CRITICAL INTEGRATION RULE:
# Only these fields are allowed to enter the frozen triage engine.
TRIAGE_FIELDS = {
    "AGE", "TEMPF", "PULSE", "RESPR", "BPSYS", "BPDIAS", "POPCT", "PAINSCALE",
    "TOTCHRON", "SEX", "ARREMS", "AMBTRANSFER", "SEEN72", "EPISODE",
    "RFV1", "RFV2", "RFV3", "RFV4", "RFV5",
    "ARTHRTIS", "ASTHMA", "CANCER", "CEBD", "CEBVD", "CHF", "CKD", "COPD",
    "DEPRN", "DIABETES", "HYPERLIP", "HYPLIPID", "HYLIPID", "HTN", "IHD", "CAD",
    "OBESITY", "OSA", "OSTEOPOR", "OSTPRSIS", "SUBSTAB",
}


def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def engine_error(label: str, exc: Exception):
    """Return an actionable integration error without leaking local paths/tracebacks."""
    message = str(exc)
    if isinstance(exc, FileNotFoundError):
        message = "required configured asset is unavailable"
    return error(f"{label} unavailable: {message}", 503)


def get_encounter(encounter_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_dict(conn.execute(
            "SELECT * FROM encounters WHERE encounter_id = ?", (encounter_id,)
        ).fetchone())


def review_for_encounter(encounter_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_dict(conn.execute(
            "SELECT * FROM doctor_reviews WHERE encounter_id=?", (encounter_id,)
        ).fetchone())


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/config")
def config():
    return jsonify(bridge.config())


@app.get("/health")
def health():
    cfg = bridge.config()
    return jsonify({
        "status": "ok",
        "database": "ready",
        "triage_engine": cfg["triage"]["ready"],
        "referral_engine": cfg["referral"]["ready"],
        "condition_pipeline": cfg["condition_pipeline"]["mode"],
    })


@app.get("/api/conditions")
def conditions():
    return jsonify({"conditions": bridge.condition_choices()})


@app.get("/api/villages")
def villages():
    return jsonify({"villages": bridge.villages()})


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    patient_id = str(data.get("patient_id", "")).strip()
    patient_name = str(data.get("patient_name", "")).strip() or None
    phone = str(data.get("phone", "")).strip() or None
    age_raw = data.get("age")
    patient_flow = str(data.get("patient_flow", "NORMAL")).upper().strip()
    sex = str(data.get("sex", "")).upper().strip() or None
    village_id = str(data.get("village_id", "")).strip() or None
    arrival = str(data.get("arrival_datetime", "")).strip()

    if not patient_id:
        return error("patient_id is required")
    try:
        age = int(age_raw) if age_raw not in {None, ""} else None
    except (TypeError, ValueError):
        return error("age must be a whole number")
    if age is not None and not 0 <= age <= 130:
        return error("age must be between 0 and 130")
    if patient_flow not in {"NORMAL", "SPECIAL"}:
        return error("patient_flow must be NORMAL or SPECIAL")
    if sex not in {None, "FEMALE", "MALE", "OTHER"}:
        return error("sex must be FEMALE, MALE, OTHER, or blank")
    if patient_flow == "NORMAL" and not village_id:
        return error("village_id is required for NORMAL flow")
    if not arrival:
        arrival = datetime.now().isoformat(timespec="seconds")
    try:
        datetime.fromisoformat(arrival.replace("Z", "+00:00"))
    except ValueError:
        return error("arrival_datetime must be ISO-8601")

    encounter_id = "ENC_" + uuid.uuid4().hex[:10].upper()
    with connect() as conn:
        conn.execute(
            """INSERT INTO patients(patient_id, full_name, phone, age, sex, village_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(patient_id) DO UPDATE SET
                 full_name=COALESCE(excluded.full_name, patients.full_name),
                 phone=COALESCE(excluded.phone, patients.phone), age=COALESCE(excluded.age, patients.age),
                 sex=COALESCE(excluded.sex, patients.sex), village_id=COALESCE(excluded.village_id, patients.village_id),
                 updated_at=excluded.updated_at""",
            (patient_id, patient_name, phone, age, sex, village_id, now_iso(), now_iso()),
        )
        conn.execute(
            """INSERT INTO encounters
               (encounter_id, patient_id, patient_flow, sex, village_id, arrival_datetime, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'REGISTERED', ?)""",
               (encounter_id, patient_id, patient_flow, sex, village_id, arrival, now_iso()),
        )
    return jsonify({"encounter_id": encounter_id, "patient_id": patient_id, "patient_flow": patient_flow, "status": "REGISTERED"})


@app.get("/api/patients")
def patients():
    with connect() as conn:
        rows = conn.execute(
            """SELECT p.*, COUNT(e.encounter_id) AS encounter_count, MAX(e.arrival_datetime) AS last_arrival
               FROM patients p LEFT JOIN encounters e USING(patient_id)
               GROUP BY p.patient_id ORDER BY last_arrival DESC, p.created_at DESC"""
        ).fetchall()
    return jsonify({"patients": [dict(row) for row in rows]})


@app.get("/api/patients/<patient_id>")
def patient_detail(patient_id: str):
    with connect() as conn:
        patient = row_dict(conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone())
        if not patient:
            return error("Patient not found", 404)
        encounters = [dict(row) for row in conn.execute(
            "SELECT * FROM encounters WHERE patient_id=? ORDER BY arrival_datetime DESC", (patient_id,)
        ).fetchall()]
    return jsonify({"patient": patient, "encounters": encounters})


@app.get("/api/encounters")
def encounters():
    """Active-case list for ASHA and doctor dashboards, sourced entirely from SQLite."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*, p.full_name, t.criticality, c.suspected_condition,
                      r.status AS referral_status, s.priority, s.priority_label,
                      d.reviewed AS doctor_reviewed, d.referral_required
               FROM encounters e
               LEFT JOIN patients p USING(patient_id)
               LEFT JOIN triage_results t USING(encounter_id)
               LEFT JOIN condition_results c USING(encounter_id)
               LEFT JOIN referrals r USING(encounter_id)
               LEFT JOIN special_cases s USING(encounter_id)
               LEFT JOIN doctor_reviews d USING(encounter_id)
               ORDER BY e.arrival_datetime DESC"""
        ).fetchall()
    return jsonify({"encounters": [dict(row) for row in rows]})


@app.post("/api/triage/<encounter_id>")
def triage(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    if encounter["patient_flow"] != "NORMAL":
        return error("SPECIAL patients do not use the normal triage/referral route")

    raw = request.get_json(silent=True) or {}
    # The barrier that prevents referral/symptom fields from disturbing triage.
    triage_payload = {k: v for k, v in raw.items() if k.upper() in TRIAGE_FIELDS}
    # Normalize frontend keys to uppercase without adding anything new.
    triage_payload = {str(k).upper(): v for k, v in triage_payload.items()}

    try:
        result = bridge.predict_triage(triage_payload)
    except Exception as exc:
        return engine_error("Triage engine", exc)

    criticality = str(result.get("final_class", "")).upper().strip()
    if criticality not in {"RED", "YELLOW", "GREEN"}:
        return error("Triage engine did not return a valid final_class", 500)

    with connect() as conn:
        conn.execute(
            """INSERT INTO triage_results(encounter_id, criticality, input_json, result_json, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(encounter_id) DO UPDATE SET
                 criticality=excluded.criticality,
                 input_json=excluded.input_json,
                 result_json=excluded.result_json,
                 created_at=excluded.created_at""",
            (encounter_id, criticality, json_text(triage_payload), json_text(result), now_iso()),
        )
        conn.execute("UPDATE encounters SET status='TRIAGED' WHERE encounter_id=?", (encounter_id,))

    return jsonify(result)


@app.post("/api/condition/<encounter_id>")
def condition(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    if encounter["patient_flow"] != "NORMAL":
        return error("SPECIAL patients use the special-priority route")

    data = request.get_json(silent=True) or {}
    symptoms = str(data.get("symptoms", "")).strip()
    suspected_condition = data.get("suspected_condition")
    if not symptoms:
        return error("symptoms are required for the separate condition pipeline")

    try:
        output = bridge.resolve_condition_output(symptoms, suspected_condition)
    except Exception as exc:
        return error(str(exc))

    condition_name = output["suspected_condition"]
    with connect() as conn:
        conn.execute(
            """INSERT INTO condition_results
               (encounter_id, symptoms, suspected_condition, pipeline_mode, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(encounter_id) DO UPDATE SET
                 symptoms=excluded.symptoms,
                 suspected_condition=excluded.suspected_condition,
                 pipeline_mode=excluded.pipeline_mode,
                 result_json=excluded.result_json,
                 created_at=excluded.created_at""",
            (
                encounter_id, symptoms, condition_name, output["mode"],
                json_text(output.get("pipeline_output")), now_iso(),
            ),
        )
        # The original prototype permits automatic review for a demo. Persist that
        # decision explicitly so a doctor can inspect or replace it before referral.
        conn.execute(
            """INSERT INTO doctor_reviews(encounter_id, reviewed, referral_required, reviewer_name, notes, reviewed_at)
               VALUES (?, 1, 1, 'Prototype auto-review', 'Auto-approved prototype gate; doctor may update.', ?)
               ON CONFLICT(encounter_id) DO NOTHING""",
            (encounter_id, now_iso()),
        )
    return jsonify({
        "suspected_condition": condition_name,
        "pipeline_mode": output["mode"],
        "note": "Symptoms remain separate from the triage engine and merge only before referral.",
    })


@app.get("/api/doctor-reviews")
def pending_doctor_reviews():
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.encounter_id, e.patient_id, p.full_name, e.village_id, e.arrival_datetime,
                      t.criticality, c.suspected_condition, d.reviewed, d.referral_required,
                      d.reviewer_name, d.notes, d.reviewed_at
               FROM encounters e
               JOIN triage_results t USING(encounter_id)
               JOIN condition_results c USING(encounter_id)
               LEFT JOIN patients p USING(patient_id)
               LEFT JOIN doctor_reviews d USING(encounter_id)
               WHERE e.patient_flow='NORMAL'
               ORDER BY e.arrival_datetime ASC"""
        ).fetchall()
    return jsonify({"reviews": [dict(row) for row in rows]})


@app.post("/api/doctor-review/<encounter_id>")
def doctor_review(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    if encounter["patient_flow"] != "NORMAL":
        return error("Doctor review gate is only used by NORMAL cases")
    data = request.get_json(silent=True) or {}
    reviewed = bool(data.get("reviewed", True))
    referral_required = bool(data.get("referral_required", True))
    reviewer_name = str(data.get("reviewer_name", "Doctor")).strip() or "Doctor"
    notes = str(data.get("notes", "")).strip() or None
    if not reviewed:
        referral_required = False
    with connect() as conn:
        conn.execute(
            """INSERT INTO doctor_reviews(encounter_id, reviewed, referral_required, reviewer_name, notes, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(encounter_id) DO UPDATE SET reviewed=excluded.reviewed,
                 referral_required=excluded.referral_required, reviewer_name=excluded.reviewer_name,
                 notes=excluded.notes, reviewed_at=excluded.reviewed_at""",
            (encounter_id, int(reviewed), int(referral_required), reviewer_name, notes, now_iso()),
        )
        conn.execute("UPDATE encounters SET status=? WHERE encounter_id=?", (
            "DOCTOR_REVIEWED" if reviewed else "AWAITING_REVIEW", encounter_id
        ))
    return jsonify({"reviewed": reviewed, "referral_required": referral_required, "reviewer_name": reviewer_name})


@app.post("/api/referral/<encounter_id>")
def referral(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    if encounter["patient_flow"] != "NORMAL":
        return error("SPECIAL patients bypass the normal referral engine")

    with connect() as conn:
        triage_row = conn.execute(
            "SELECT * FROM triage_results WHERE encounter_id=?", (encounter_id,)
        ).fetchone()
        condition_row = conn.execute(
            "SELECT * FROM condition_results WHERE encounter_id=?", (encounter_id,)
        ).fetchone()

    if not triage_row:
        return error("Triage must run before referral")
    if not condition_row:
        return error("Condition/symptom pipeline must complete before referral")
    review = review_for_encounter(encounter_id)
    if not review or not review["reviewed"]:
        return error("Doctor review must be completed before referral")
    if not review["referral_required"]:
        return error("Doctor review marked this case as not requiring referral", 409)

    # ---------------- BACKEND MERGER ----------------
    # Registration data + triage output + symptom-pipeline output meet HERE only.
    referral_payload = {
        "patient_id": encounter["patient_id"],
        "patient_flow": encounter["patient_flow"],
        "doctor_reviewed": bool(review["reviewed"]),
        "referral_required": bool(review["referral_required"]),
        # ONLY from frozen triage engine output stored in DB.
        "criticality": triage_row["criticality"],
        # ONLY from parallel symptom/condition pipeline stored in DB.
        "suspected_condition": condition_row["suspected_condition"],
        # No GPS: referral engine resolves this village from villages.csv.
        "patient_location": {"village_id": encounter["village_id"]},
        "arrival_datetime": encounter["arrival_datetime"],
    }

    try:
        result = bridge.recommend_referral(referral_payload, top_k=3)
    except Exception as exc:
        return engine_error("Referral engine", exc)

    with connect() as conn:
        conn.execute(
            """INSERT INTO referrals(encounter_id, payload_json, result_json, status, created_at)
               VALUES (?, ?, ?, 'RANKED', ?)
               ON CONFLICT(encounter_id) DO UPDATE SET
                 payload_json=excluded.payload_json,
                 result_json=excluded.result_json,
                 selected_facility_id=NULL,
                 selected_facility_name=NULL,
                 status='RANKED',
                 created_at=excluded.created_at""",
            (encounter_id, json_text(referral_payload), json_text(result), now_iso()),
        )
        conn.execute("UPDATE encounters SET status='REFERRAL_RANKED' WHERE encounter_id=?", (encounter_id,))

    return jsonify({"merged_payload": referral_payload, "referral_result": result})


@app.post("/api/referral/<encounter_id>/confirm")
def confirm_referral(encounter_id: str):
    data = request.get_json(silent=True) or {}
    facility_id = str(data.get("facility_id", "")).strip()
    if not facility_id:
        return error("facility_id is required")

    with connect() as conn:
        row = conn.execute("SELECT result_json FROM referrals WHERE encounter_id=?", (encounter_id,)).fetchone()
        if not row:
            return error("Run referral ranking first", 404)
        result = json.loads(row["result_json"])
        candidates = []
        if isinstance(result.get("recommended_facility"), dict):
            candidates.append(result["recommended_facility"])
        candidates.extend(result.get("alternatives") or [])
        chosen = next((x for x in candidates if str(x.get("facility_id")) == facility_id), None)
        if not chosen:
            return error("facility_id is not one of the ranked referral choices")
        conn.execute(
            """UPDATE referrals SET selected_facility_id=?, selected_facility_name=?, status='CONFIRMED'
               WHERE encounter_id=?""",
            (facility_id, chosen.get("facility_name"), encounter_id),
        )
        conn.execute("UPDATE encounters SET status='REFERRAL_CONFIRMED' WHERE encounter_id=?", (encounter_id,))
    return jsonify({"status": "CONFIRMED", "selected_facility": chosen})


@app.get("/api/referrals")
def referrals():
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.encounter_id, r.selected_facility_id, r.selected_facility_name, r.status,
                      r.created_at, e.patient_id, e.patient_flow
               FROM referrals r JOIN encounters e USING(encounter_id)
               ORDER BY r.created_at DESC"""
        ).fetchall()
    return jsonify({"referrals": [dict(row) for row in rows]})


@app.get("/api/referral/<encounter_id>")
def referral_detail(encounter_id: str):
    with connect() as conn:
        row = row_dict(conn.execute("SELECT * FROM referrals WHERE encounter_id=?", (encounter_id,)).fetchone())
    if not row:
        return error("Referral not found", 404)
    row["payload"] = json.loads(row.pop("payload_json"))
    row["result"] = json.loads(row.pop("result_json"))
    return jsonify(row)


@app.post("/api/special/<encounter_id>")
def special_case(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    if encounter["patient_flow"] != "SPECIAL":
        return error("This endpoint is only for SPECIAL patients")

    data = request.get_json(silent=True) or {}
    pregnant = bool(data.get("pregnant", False))
    has_other_disease = bool(data.get("has_other_disease", False))
    private_disease = bool(data.get("private_disease", False))
    disease_details = str(data.get("disease_details", "")).strip()
    private_details = str(data.get("private_disease_details", "")).strip()

    # Pregnancy categories are only valid for female patients in this prototype.
    if pregnant and encounter.get("sex") != "FEMALE":
        return error("Pregnancy priority categories require sex=FEMALE")

    # Explicit priority design requested for the prototype. Any disease combined
    # with pregnancy is P1, including a private/sensitive disease.
    has_any_disease = has_other_disease or private_disease
    if pregnant and has_any_disease:
        priority = 1
        label = "P1 — Pregnant + disease"
        route = "Gynaecologist/Obstetrician + relevant disease specialist"
    elif pregnant:
        priority = 2
        label = "P2 — Pregnant only"
        route = "Gynaecologist/Obstetrician"
    elif private_disease:
        priority = 3
        label = "P3 — Private/sensitive disease"
        route = "Appropriate private-disease specialist"
    else:
        return error("SPECIAL patient must match P1, P2, or P3 criteria")

    with connect() as conn:
        conn.execute(
            """INSERT INTO special_cases
               (encounter_id,is_pregnant,has_other_disease,private_disease,disease_details,
                private_disease_details,priority,priority_label,specialist_route,queue_status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'WAITING',?)
               ON CONFLICT(encounter_id) DO UPDATE SET
                 is_pregnant=excluded.is_pregnant,
                 has_other_disease=excluded.has_other_disease,
                 private_disease=excluded.private_disease,
                 disease_details=excluded.disease_details,
                 private_disease_details=excluded.private_disease_details,
                 priority=excluded.priority,
                 priority_label=excluded.priority_label,
                 specialist_route=excluded.specialist_route,
                 queue_status='WAITING',
                 created_at=excluded.created_at""",
            (
                encounter_id, int(pregnant), int(has_other_disease), int(private_disease),
                disease_details, private_details, priority, label, route, now_iso(),
            ),
        )
        conn.execute("UPDATE encounters SET status='SPECIAL_QUEUED' WHERE encounter_id=?", (encounter_id,))

    return jsonify({"priority": priority, "priority_label": label, "specialist_route": route})


@app.get("/api/special-queue")
def special_queue():
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, e.patient_id, e.sex, e.arrival_datetime
               FROM special_cases s JOIN encounters e USING(encounter_id)
               WHERE s.queue_status='WAITING'
               ORDER BY s.priority ASC, e.arrival_datetime ASC, s.created_at ASC"""
        ).fetchall()
    return jsonify({"queue": [dict(r) for r in rows]})


@app.post("/api/followup/<encounter_id>")
def followup(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    data = request.get_json(silent=True) or {}
    outcome = str(data.get("outcome", "")).strip()
    notes = str(data.get("notes", "")).strip()
    status = str(data.get("status", "OPEN")).upper().strip()
    if status not in {"OPEN", "COMPLETED", "CLOSED"}:
        return error("status must be OPEN, COMPLETED, or CLOSED")
    with connect() as conn:
        conn.execute(
            "INSERT INTO followups(encounter_id,outcome,notes,status,created_at) VALUES (?,?,?,?,?)",
            (encounter_id, outcome, notes, status, now_iso()),
        )
        conn.execute("UPDATE encounters SET status=? WHERE encounter_id=?", (
            "CLOSED" if status == "CLOSED" else "FOLLOWUP", encounter_id
        ))
    return jsonify({"status": status, "message": "Follow-up recorded"})


@app.post("/api/treatment/<encounter_id>")
def treatment(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    data = request.get_json(silent=True) or {}
    notes = str(data.get("notes", "")).strip()
    clinician_name = str(data.get("clinician_name", "Doctor")).strip() or "Doctor"
    if not notes:
        return error("treatment notes are required")
    with connect() as conn:
        conn.execute(
            "INSERT INTO treatments(encounter_id, clinician_name, notes, created_at) VALUES (?, ?, ?, ?)",
            (encounter_id, clinician_name, notes, now_iso()),
        )
        conn.execute("UPDATE encounters SET status='IN_TREATMENT' WHERE encounter_id=?", (encounter_id,))
    return jsonify({"message": "Treatment record saved", "status": "IN_TREATMENT"})


@app.get("/api/followups")
def list_followups():
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.*, e.patient_id, e.patient_flow, e.status AS encounter_status
               FROM followups f JOIN encounters e USING(encounter_id)
               ORDER BY f.created_at DESC"""
        ).fetchall()
    return jsonify({"followups": [dict(row) for row in rows]})


@app.get("/api/dashboard")
def dashboard():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
        normal = conn.execute("SELECT COUNT(*) FROM encounters WHERE patient_flow='NORMAL'").fetchone()[0]
        special = total - normal
        priorities = {f"P{priority}": conn.execute(
            "SELECT COUNT(*) FROM special_cases WHERE priority=?", (priority,)
        ).fetchone()[0] for priority in (1, 2, 3)}
        criticalities = {label: conn.execute(
            "SELECT COUNT(*) FROM triage_results WHERE criticality=?", (label,)
        ).fetchone()[0] for label in ("RED", "YELLOW", "GREEN")}
        active_referrals = conn.execute("SELECT COUNT(*) FROM referrals WHERE status='CONFIRMED'").fetchone()[0]
        pending_followups = conn.execute("SELECT COUNT(*) FROM followups WHERE status='OPEN'").fetchone()[0]
    return jsonify({"total_patients": total, "normal_patients": normal, "special_patients": special,
                    "priorities": priorities, "criticalities": criticalities,
                    "active_referrals": active_referrals, "pending_followups": pending_followups})


@app.get("/api/encounter/<encounter_id>")
def encounter_summary(encounter_id: str):
    encounter = get_encounter(encounter_id)
    if not encounter:
        return error("Encounter not found", 404)
    with connect() as conn:
        triage = row_dict(conn.execute("SELECT * FROM triage_results WHERE encounter_id=?", (encounter_id,)).fetchone())
        condition = row_dict(conn.execute("SELECT * FROM condition_results WHERE encounter_id=?", (encounter_id,)).fetchone())
        referral_row = row_dict(conn.execute("SELECT * FROM referrals WHERE encounter_id=?", (encounter_id,)).fetchone())
        special = row_dict(conn.execute("SELECT * FROM special_cases WHERE encounter_id=?", (encounter_id,)).fetchone())
        followups = [dict(r) for r in conn.execute("SELECT * FROM followups WHERE encounter_id=? ORDER BY followup_id", (encounter_id,)).fetchall()]
        review = row_dict(conn.execute("SELECT * FROM doctor_reviews WHERE encounter_id=?", (encounter_id,)).fetchone())
        treatments = [dict(r) for r in conn.execute("SELECT * FROM treatments WHERE encounter_id=? ORDER BY treatment_id", (encounter_id,)).fetchall()]
    if triage:
        triage["result"] = json.loads(triage.pop("result_json"))
        triage.pop("input_json", None)
    if referral_row:
        referral_row["payload"] = json.loads(referral_row.pop("payload_json"))
        referral_row["result"] = json.loads(referral_row.pop("result_json"))
    return jsonify({
        "encounter": encounter,
        "triage": triage,
        "condition": condition,
        "referral": referral_row,
        "special": special,
        "doctor_review": review,
        "treatments": treatments,
        "followups": followups,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
