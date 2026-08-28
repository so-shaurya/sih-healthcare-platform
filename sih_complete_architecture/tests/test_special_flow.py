"""Offline smoke test: special flow requires no ML asset and must stay persistent."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class SpecialFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        import app
        import db
        db.DB_PATH = Path(self.temp.name) / "test.db"
        app.app.config.update(TESTING=True)
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def register_and_classify(self, patient_id: str, arrival: str, sex: str = "FEMALE", **case):
        registered = self.client.post("/api/register", json={
            "patient_id": patient_id, "patient_flow": "SPECIAL", "sex": sex,
            "arrival_datetime": arrival,
        })
        self.assertEqual(registered.status_code, 200)
        encounter_id = registered.get_json()["encounter_id"]
        classified = self.client.post(f"/api/special/{encounter_id}", json=case)
        self.assertEqual(classified.status_code, 200)
        return encounter_id

    def test_priority_then_arrival_order_and_followup(self):
        p2 = self.register_and_classify("P2", "2026-08-26T09:50:00", pregnant=True, has_other_disease=False, private_disease=False)
        self.register_and_classify("P1LATE", "2026-08-26T10:10:00", pregnant=True, has_other_disease=True, private_disease=False)
        self.register_and_classify("P1EARLY", "2026-08-26T10:05:00", pregnant=True, has_other_disease=True, private_disease=False)
        self.register_and_classify("P3", "2026-08-26T09:30:00", sex="MALE", pregnant=False, has_other_disease=False, private_disease=True)
        queue = self.client.get("/api/special-queue").get_json()["queue"]
        self.assertEqual([row["patient_id"] for row in queue], ["P1EARLY", "P1LATE", "P2", "P3"])
        followup = self.client.post(f"/api/followup/{p2}", json={"outcome": "Seen", "status": "CLOSED"})
        self.assertEqual(followup.status_code, 200)


if __name__ == "__main__":
    unittest.main()
