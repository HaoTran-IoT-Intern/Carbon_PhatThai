#!/usr/bin/env python3
"""
CEMS Mock Backend Cloud Server — Hướng 1 v2
Implements API Contract Spec v1.2.3:
- POST /api/v1/cems/telemetry/batch (Two-tier Idempotency SHA-256 + Unique Reading Key)
- HTTP 200/400/401/403/409/429/500 responses
- Backward compatible: POST /receive, GET /readings, GET /health
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
legacy_data_store = []
legacy_received_count = 0


def reset_mock_server_state():
    """Resets all backend in-memory data and simulation flags."""
    global legacy_received_count
    batch_db.clear()
    readings_db.clear()
    legacy_data_store.clear()
    legacy_received_count = 0
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
    if not payload:
        return jsonify({"status": "error", "message": "Missing or invalid JSON"}), 400

    batch_id = payload.get("batch_id")
    readings = payload.get("readings", [])
    if not batch_id or not isinstance(readings, list):
        return jsonify({"status": "error", "message": "Invalid batch schema"}), 400

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


# ==============================================================================
# Backward-Compatible Endpoints (POC v1/v2)
# ==============================================================================
@app.route('/receive', methods=['POST'])
def receive():
    global legacy_received_count
    legacy_received_count += 1

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({'status': 'error', 'message': 'No JSON payload'}), 400

    record = {
        'id': legacy_received_count,
        'device_id': payload.get('device_id'),
        'ts': payload.get('ts'),
        'location': payload.get('location'),
        'co2_pct': payload.get('co2_pct'),
        'so2_ppm': payload.get('so2_ppm'),
        'nox_ppm': payload.get('nox_ppm'),
        'co_ppm': payload.get('co_ppm'),
        'o2_pct': payload.get('o2_pct'),
        'pm_mgm3': payload.get('pm_mgm3'),
        'flow_m3h': payload.get('flow_m3h'),
        'temp_c': payload.get('temp_c'),
        'pressure_atm': payload.get('pressure_atm'),
        'humidity_pct': payload.get('humidity_pct'),
        'zero_drift_pct': payload.get('zero_drift_pct'),
        'span_drift_pct': payload.get('span_drift_pct'),
        'qaqc_status': payload.get('qaqc_status'),
        'last_rata_date': payload.get('last_rata_date'),
        'rata_accuracy_pct': payload.get('rata_accuracy_pct'),
        'received_at': datetime.now(timezone.utc).isoformat()
    }
    legacy_data_store.append(record)
    return jsonify({'status': 'ok', 'id': legacy_received_count})


@app.route('/readings', methods=['GET'])
def get_readings():
    return jsonify({
        'status': 'ok',
        'count': len(legacy_data_store),
        'data': legacy_data_store
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'services': ['mqtt-gateway', 'flask-server'],
        'received_count': legacy_received_count,
        'data_count': len(legacy_data_store),
        'batch_count': len(batch_db),
        'reading_count': len(readings_db)
    })


if __name__ == '__main__':
    print('[SERVER] Flask Server starting on http://0.0.0.0:5001')
    print('[SERVER] POST /api/v1/cems/telemetry/batch — CEMS Telemetry Batch Ingestion')
    print('[SERVER] POST /receive — Legacy endpoint')
    print('[SERVER] GET /readings — Legacy readings store')
    print('[SERVER] GET /health — Health status')
    app.run(host='0.0.0.0', port=5001, debug=False)
