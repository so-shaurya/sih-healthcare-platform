"""Local web API for the v6 two-stage NHAMCS triage prototype.

Start this with the SAME Python environment used to train ``second_test.py``
(it needs numpy, pandas, and scikit-learn):

    python triage_api.py

The server binds only to 127.0.0.1 and does not save request bodies.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BUNDLE_PATH = ROOT / "artifacts" / "triage_safety_pipeline_v6.pkl"
WEB_PAGE = ROOT / "web" / "index.html"
HOST, PORT = "127.0.0.1", 8000

if not BUNDLE_PATH.exists():
    raise FileNotFoundError(f"v6 model bundle not found: {BUNDLE_PATH}")

# Reusing the prediction function is essential: it applies exactly the same
# v6 preprocessing, two-stage inference, RFV risk flags, and safety guard as
# the training script's --predict-json command.
from second_test import predict_patient  # noqa: E402


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: HTTPStatus, data: dict[str, Any]) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        page = WEB_PAGE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/triage":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20_000:
                raise ValueError("Send one JSON patient object (maximum 20 KB).")
            patient = json.loads(self.rfile.read(length))
            if not isinstance(patient, dict):
                raise ValueError("Request body must be a JSON object.")
            self._send_json(HTTPStatus.OK, predict_patient(patient, BUNDLE_PATH))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, *_: Any) -> None:
        """Do not log requests, which could contain patient information."""


if __name__ == "__main__":
    print(f"v6 local triage demo: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), RequestHandler).serve_forever()
