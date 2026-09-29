#!/usr/bin/env python3
"""
CEMS Mock Backend Cloud Server
Implements API Contract Spec v1.2.3:
- POST /api/v1/cems/telemetry/batch (Two-tier Idempotency SHA-256 + Unique Reading Key)
- HTTP 200/400/401/403/409/429/500 responses
"""

import json
import hashlib
from urllib.parse import urlparse
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)
VALID_API_KEY = "cems_gw_secret_key_prod_xxxxxxxxx"

# In-memory storage for cloud simulation
batch_db = {}
readings_db = {}


def reset_mock_server_state():
    """Resets all backend in-memory data and simulation flags."""
    batch_db.clear()
    readings_db.clear()
    app.config["SIMULATE_DB_ERROR"] = False
    app.config["SIMULATE_RATE_LIMIT"] = False
    app.config["SIMULATE_FAIL_ON_READING_INDEX"] = None
    app.config["ALLOWED_STACKS"] = None


class FlaskTestClientAdapter:
    """
    Adapts Flask test_client to a requests-like interface for gateway.dispatcher.
    Translates post() call and wraps response with status_code, headers, text, and json() method.
    """
    def __init__(self, test_client):
        self.test_client = test_client

    def post(self, url, headers=None, data=None, timeout=None):
        path = urlparse(url).path
        if not path.startswith('/'):
            path = '/' + path
        flask_resp = self.test_client.post(path, headers=dict(headers or {}), data=data)

        class AdaptedResponse:
            def __init__(self, r):
                self.status_code = r.status_code
                self.headers = dict(r.headers)
                self.text = r.get_data(as_text=True)

            def json(self):
                try:
                    return json.loads(self.text)
                except Exception:
                    return {"text": self.text}

        return AdaptedResponse(flask_resp)


# ==============================================================================
# Spec v1.2.3 Cloud Ingestion Endpoint
# ==============================================================================
@app.route('/api/v1/cems/telemetry/batch', methods=['POST'])
def receive_batch():
    # 1. Check API Key (401 Unauthorized)
    auth_header = request.headers.get('X-API-Key')
    if auth_header != VALID_API_KEY:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    # 2. Check JSON payload (400 Bad Request)
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Missing or invalid JSON body"}), 400

    schema_version = payload.get("schema_version")
    if schema_version != "1.2.3":
        return jsonify({"status": "error", "message": f"Unsupported or invalid schema_version: {schema_version}. Expected '1.2.3'"}), 400

    batch_id = payload.get("batch_id")
    gateway_id = payload.get("gateway_id")
    batch_created_at = payload.get("batch_created_at")
    readings = payload.get("readings")

    if not batch_id or not isinstance(batch_id, str):
        return jsonify({"status": "error", "message": "batch_id must be a non-empty string"}), 400
    if not gateway_id or not isinstance(gateway_id, str):
        return jsonify({"status": "error", "message": "gateway_id must be a non-empty string"}), 400
    if not batch_created_at or not isinstance(batch_created_at, str):
        return jsonify({"status": "error", "message": "batch_created_at must be a non-empty string"}), 400
    if not isinstance(readings, list) or len(readings) == 0 or len(readings) > 10:
        return jsonify({"status": "error", "message": "Readings count must be between 1 and 10"}), 400

    required_reading_fields = [
        "stack_id", "device_id", "measurement_config_id", "ts",
        "window_seconds", "samples", "gases_and_dust", "flue_gas",
        "ambient", "integrated_emissions", "qaqc"
    ]
    for idx, r in enumerate(readings):
        if not isinstance(r, dict):
            return jsonify({"status": "error", "message": f"Reading #{idx+1} is not a valid JSON object"}), 400
        for f in required_reading_fields:
            if f not in r or r[f] is None:
                return jsonify({"status": "error", "message": f"Reading #{idx+1} missing required field: {f}"}), 400
        if not isinstance(r["samples"], dict) or "valid_by_metric" not in r["samples"] or "excluded_by_reason" not in r["samples"]:
            return jsonify({"status": "error", "message": f"Reading #{idx+1} has invalid samples schema"}), 400

    # 3. Check Stack Authorization (403 Forbidden)
    allowed_stacks = app.config.get("ALLOWED_STACKS")
    if allowed_stacks is not None:
        for r in readings:
            stk = r.get("stack_id")
            if stk and stk not in allowed_stacks:
                return jsonify({"status": "error", "message": f"Forbidden: stack {stk} not authorized"}), 403

    # Simulation hooks for testing (500 and 429)
    if app.config.get("SIMULATE_DB_ERROR") or request.headers.get("X-Simulate-Error") == "500":
        return jsonify({"status": "error", "message": "Database transaction aborted during batch processing"}), 500

    if app.config.get("SIMULATE_RATE_LIMIT") or request.headers.get("X-Simulate-Error") == "429":
        resp = jsonify({"status": "error", "message": "Rate limit exceeded"})
        resp.headers["Retry-After"] = "15"
        return resp, 429

    # Tier 1 Idempotency: Canonical SHA-256 hash on UTF-8 bytes of payload_json
    payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    if batch_id in batch_db:
        if batch_db[batch_id]["payload_hash"] == payload_hash:
            return jsonify({
                "status": "success",
                "batch_id": batch_id,
                "processed_count": len(readings),
                "inserted_count": 0,
                "duplicated_count": len(readings),
                "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            }), 200
        else:
            return jsonify({"status": "error", "message": "Conflict payload for existing batch_id"}), 409

    # Tier 2 Idempotency: Process readings within an atomic transaction
    simulate_fail_index = app.config.get("SIMULATE_FAIL_ON_READING_INDEX")
    temp_inserted_keys = []
    inserted = 0
    duplicated = 0
    try:
        for idx, r in enumerate(readings):
            if simulate_fail_index is not None and idx == simulate_fail_index:
                raise RuntimeError(f"Simulated DB crash while inserting reading #{idx + 1}")

            key = (r.get("stack_id"), r.get("ts"))
            if key in readings_db:
                if readings_db[key] == r:
                    duplicated += 1
                else:
                    # Rollback all keys inserted in this batch transaction
                    for k in temp_inserted_keys:
                        readings_db.pop(k, None)
                    return jsonify({"status": "error", "message": f"Conflict data for {key}"}), 409
            else:
                readings_db[key] = r
                temp_inserted_keys.append(key)
                inserted += 1

        batch_db[batch_id] = {
            "payload_hash": payload_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "readings_count": len(readings)
        }
        return jsonify({
            "status": "success",
            "batch_id": batch_id,
            "processed_count": len(readings),
            "inserted_count": inserted,
            "duplicated_count": duplicated,
            "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        }), 200
    except Exception as e:
        for k in temp_inserted_keys:
            readings_db.pop(k, None)
        return jsonify({"status": "error", "message": f"Transaction error: {str(e)}"}), 500


if __name__ == '__main__':
    print('[SERVER] Flask Server starting on http://0.0.0.0:5001')
    print('[SERVER] POST /api/v1/cems/telemetry/batch — CEMS Telemetry Batch Ingestion')
    app.run(host='0.0.0.0', port=5001, debug=False)
