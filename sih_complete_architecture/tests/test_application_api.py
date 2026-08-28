"""Integration tests for the canonical local Flask/SQLite application."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class ApplicationApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        import app
        import db
        db.DB_PATH = Path(self.temp.name) / "application.db"
        app.app.config.update(TESTING=True)
        app.init_db()
        self.app = app
        self.client = app.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def register_normal(self):
        response = self.client.post("/api/register", json={
            "patient_id": "NORMAL_001", "patient_name": "Test Patient", "age": 31,
            "phone": "9999999999", "patient_flow": "NORMAL", "sex": "FEMALE",
            "village_id": "VIL151", "arrival_datetime": "2026-08-26T10:00:00",
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()["encounter_id"]

    def test_health_registration_dashboard_and_real_triage_state(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["triage_engine"])
        self.assertTrue(health.get_json()["referral_engine"])
        encounter_id = self.register_normal()
        detail = self.client.get("/api/patients/NORMAL_001")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["patient"]["full_name"], "Test Patient")
        triage = self.client.post(f"/api/triage/{encounter_id}", json={
            "AGE": 31, "SEX": 1, "TEMPF": 98.6, "PULSE": 82, "RESPR": 16,
            "BPSYS": 120, "BPDIAS": 80, "POPCT": 98, "PAINSCALE": 2, "TOTCHRON": 0,
        })
        self.assertEqual(triage.status_code, 200)
        self.assertIn(triage.get_json()["final_class"], {"RED", "YELLOW", "GREEN"})
        dashboard = self.client.get("/api/dashboard").get_json()
        self.assertEqual(dashboard["normal_patients"], 1)

    def test_real_normal_workflow_and_persistence(self):
        encounter_id = self.register_normal()
        triage = self.client.post(f"/api/triage/{encounter_id}", json={
            "AGE": 35, "SEX": 1, "TEMPF": 98.6, "PULSE": 82, "RESPR": 16,
            "BPSYS": 120, "BPDIAS": 80, "POPCT": 98, "PAINSCALE": 2, "TOTCHRON": 0,
        })
        self.assertEqual(triage.status_code, 200)
        condition = self.client.post(f"/api/condition/{encounter_id}", json={
            "symptoms": "Fall, swelling, and severe leg pain", "suspected_condition": "Fracture",
        })
        self.assertEqual(condition.status_code, 200)
        review = self.client.post(f"/api/doctor-review/{encounter_id}", json={
            "reviewed": True, "referral_required": True, "reviewer_name": "Dr Actual",
        })
        self.assertEqual(review.status_code, 200)
        ranked = self.client.post(f"/api/referral/{encounter_id}", json={})
        self.assertEqual(ranked.status_code, 200)
        result = ranked.get_json()["referral_result"]
        self.assertEqual(result["status"], "pending_doctor_confirmation")
        chosen = result["recommended_facility"]
        confirmed = self.client.post(f"/api/referral/{encounter_id}/confirm", json={"facility_id": chosen["facility_id"]})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.get_json()["status"], "CONFIRMED")
        self.assertEqual(self.client.post(f"/api/treatment/{encounter_id}", json={"clinician_name": "Dr Actual", "notes": "Referral prepared"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/followup/{encounter_id}", json={"outcome": "Transferred", "status": "CLOSED"}).status_code, 200)
        # New client simulates access after an application/process restart: all state is SQLite-backed.
        restarted_client = self.app.app.test_client()
        persisted = restarted_client.get(f"/api/encounter/{encounter_id}").get_json()
        self.assertEqual(persisted["encounter"]["status"], "CLOSED")
        self.assertEqual(persisted["triage"]["criticality"], triage.get_json()["final_class"])
        self.assertEqual(persisted["condition"]["suspected_condition"], "Fracture")
        self.assertEqual(persisted["referral"]["status"], "CONFIRMED")
        self.assertEqual(len(persisted["treatments"]), 1)
        self.assertEqual(len(persisted["followups"]), 1)

    def test_doctor_review_treatment_and_case_closure(self):
        encounter_id = self.register_normal()
        with self.app.connect() as conn:
            conn.execute(
                "INSERT INTO triage_results VALUES (?, 'YELLOW', '{}', '{}', '2026-08-26T10:01:00')",
                (encounter_id,),
            )
            conn.execute(
                "INSERT INTO condition_results VALUES (?, 'Pain', 'Fracture', 'test', '{}', '2026-08-26T10:02:00')",
                (encounter_id,),
            )
        review = self.client.post(f"/api/doctor-review/{encounter_id}", json={
            "reviewed": True, "referral_required": False, "reviewer_name": "Dr Test",
        })
        self.assertEqual(review.status_code, 200)
        self.assertFalse(review.get_json()["referral_required"])
        treatment = self.client.post(f"/api/treatment/{encounter_id}", json={"notes": "Local treatment", "clinician_name": "Dr Test"})
        self.assertEqual(treatment.status_code, 200)
        closed = self.client.post(f"/api/followup/{encounter_id}", json={"outcome": "Recovered", "status": "CLOSED"})
        self.assertEqual(closed.status_code, 200)
        summary = self.client.get(f"/api/encounter/{encounter_id}").get_json()
        self.assertEqual(summary["encounter"]["status"], "CLOSED")
        self.assertEqual(len(summary["treatments"]), 1)
