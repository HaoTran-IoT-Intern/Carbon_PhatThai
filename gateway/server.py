#!/usr/bin/env python3
"""
CEMS Server Flask — Hướng 1 v2
Nhận POST /receive từ Gateway, lưu data, cung cấp API GET /readings
"""

from flask import Flask, request, jsonify
from datetime import datetime, timezone

app = Flask(__name__)

# ===== STORE TRONG MEMORY (POC) =====
data_store = []
received_count = 0


@app.route('/receive', methods=['POST'])
def receive():
    global received_count
    received_count += 1

    payload = request.get_json()
    if not payload:
        return jsonify({'status': 'error', 'message': 'No JSON payload'}), 400

    record = {
        'id': received_count,
        'device_id': payload.get('device_id'),
        'ts': payload.get('ts'),
        'location': payload.get('location'),
        # 6 khí + bụi
        'co2_pct': payload.get('co2_pct'),
        'so2_ppm': payload.get('so2_ppm'),
        'nox_ppm': payload.get('nox_ppm'),
        'co_ppm': payload.get('co_ppm'),
        'o2_pct': payload.get('o2_pct'),
        'pm_mgm3': payload.get('pm_mgm3'),
        # 3 vật lý + môi trường
        'flow_m3h': payload.get('flow_m3h'),
        'temp_c': payload.get('temp_c'),
        'pressure_atm': payload.get('pressure_atm'),
        'humidity_pct': payload.get('humidity_pct'),
        # QA/QC + RATA
        'zero_drift_pct': payload.get('zero_drift_pct'),
        'span_drift_pct': payload.get('span_drift_pct'),
        'qaqc_status': payload.get('qaqc_status'),
        'last_rata_date': payload.get('last_rata_date'),
        'rata_accuracy_pct': payload.get('rata_accuracy_pct'),
        'received_at': datetime.now(timezone.utc).isoformat()
    }
    data_store.append(record)

    print(f"[SERVER] Received #{received_count} from {payload.get('device_id')}")

    return jsonify({'status': 'ok', 'id': received_count})


@app.route('/readings', methods=['GET'])
def get_readings():
    return jsonify({
        'status': 'ok',
        'count': len(data_store),
        'data': data_store
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'services': ['mqtt-gateway', 'flask-server'],
        'received_count': received_count,
        'data_count': len(data_store)
    })


if __name__ == '__main__':
    print('[SERVER] Flask Server starting on http://0.0.0.0:5001')
    print('[SERVER] POST /receive — nhận data từ Gateway')
    print('[SERVER] GET /readings — xem data đã lưu')
    print('[SERVER] GET /health — health check')
    app.run(host='0.0.0.0', port=5001, debug=False)
