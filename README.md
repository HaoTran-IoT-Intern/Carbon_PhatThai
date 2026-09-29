# Pione Carbon Platform — CEMS IoT (Hướng 1)

Hệ thống đo đạc phát thải & kiểm toán tín chỉ carbon: ESP32 → Edge Gateway → Backend.

```
[ESP32 Node] --MQTT/Wi-Fi--> [Mosquitto Broker] --Python Daemon--> [Backend API]
  cảm biến 10s              Port 1883 (nội bộ)         mỗi 5 phút POST /api/v1/cems/telemetry/batch
```

## Cấu trúc

- `firmware/cems_node_01/` — Firmware ESP32 (Arduino): cảm biến CO2/SO2/NOx/CO/O2/bụi, DHT11, MQTT publish 10s, store-and-forward ring buffer
- `gateway/` — Edge Gateway Daemon (Python):
  - `gateway.py` — Main daemon, đa luồng
  - `slot_manager.py` — Chia 6 khe/phút, chống ghi đè
  - `aggregator.py` — Tích phân thể tích CO2 chuẩn (0°C, 101.325 kPa), QA/QC
  - `batch_manager.py` — Đóng băng lô 5 phút bất biến
  - `dispatcher.py` — Đẩy lên backend với retry & cooldown
  - `retention.py` — Dọn dẹp dữ liệu đã sync (>30 ngày)
  - `server.py` — Mock backend cho testing
  - `config.json` — Cấu hình gateway
- `tests/` — 73 tests (100% pass): unit + E2E
- `scripts/setup_mosquitto.sh` — Cài đặt Mosquitto broker

## Cài đặt

```bash
# 1. Mosquitto broker (macOS)
brew install mosquitto
mosquitto -c gateway/mosquitto_local.conf

# 2. Gateway daemon
python -m venv .venv && source .venv/bin/activate
pip install -r gateway/requirements.txt  # paho-mqtt, requests
python gateway/gateway.py

# 3. Mock backend (optional, cho testing)
python gateway/server.py
```

## Chạy tests

```bash
pytest tests/ -v
```

## API Contract

Gateway gửi mỗi 5 phút:

```
POST /api/v1/cems/telemetry/batch
X-API-Key: <cems_gw_secret_key>
Content-Type: application/json
```

Body: `{schema_version, batch_id, gateway_id, batch_created_at, readings[]}` — xem `MoTa.md` mục 4-5 cho payload mẫu đầy đủ và data dictionary.

Response codes: `200` (success + ack_token), `400` (quarantine), `401` (halt), `403` (suspend stack), `409` (quarantine), `429` (retry-after), `500/503` (exponential backoff).

## Trạng thái

Spec v1.2.3 — 73/73 tests passed, đã verify trên phần cứng thật (ESP32 + DHT11 + Mosquitto).
