from __future__ import annotations

from flask import Flask, jsonify, request

from referral_engine import ReferralEngine

app = Flask(__name__)
engine = ReferralEngine()


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "referral-engine"})


@app.post("/api/referral/recommend")
def recommend():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Expected a JSON request body"}), 400

    try:
        result = engine.recommend(payload, top_k=int(request.args.get("top_k", 3)))
        return jsonify(result), 200
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": "Internal referral-engine error", "detail": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
