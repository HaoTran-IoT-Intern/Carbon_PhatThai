import paho.mqtt.client as mqtt
import json
import sqlite3
import requests
from datetime import datetime, timezone
import sys
import threading
import time
from modbus_adapter import MockModbusDevice

MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "pione/carbon/v1/cems_node_01/data"
SERVER_URL = "http://127.0.0.1:5001/receive"
DB_FILE = "cems_data.db"


def migrate_db():
    """Thêm cột mới nếu chưa tồn tại (an toàn, không mất dữ liệu)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Các cột mới cần thêm
    new_columns = [
        ("so2_ppm", "REAL"),
        ("nox_ppm", "REAL"),
        ("co_ppm", "REAL"),
        ("o2_pct", "REAL"),
        ("pm_mgm3", "REAL"),
        ("humidity_pct", "REAL"),
        ("zero_drift_pct", "REAL"),
        ("span_drift_pct", "REAL"),
        ("qaqc_status", "TEXT"),
        ("last_rata_date", "TEXT"),
        ("rata_accuracy_pct", "REAL"),
    ]
    
    # Lấy danh sách cột hiện có
    c.execute("PRAGMA table_info(readings)")
    existing = {row[1] for row in c.fetchall()}
    
    for col, col_type in new_columns:
        if col not in existing:
            try:
                c.execute(f"ALTER TABLE readings ADD COLUMN {col} {col_type}")
                print(f"[Migration] Added column: {col}")
            except Exception as e:
                print(f"[Migration] Failed to add {col}: {e}")
    
    conn.commit()
    conn.close()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            ts TEXT,
            lat REAL,
            lon REAL,
            co2_pct REAL,
            flow_m3h REAL,
            temp_c REAL,
            pressure_atm REAL,
            humidity_pct REAL,
            so2_ppm REAL,
            nox_ppm REAL,
            co_ppm REAL,
            o2_pct REAL,
            pm_mgm3 REAL,
            zero_drift_pct REAL,
            span_drift_pct REAL,
            qaqc_status TEXT,
            last_rata_date TEXT,
            rata_accuracy_pct REAL,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()
    migrate_db()  # chạy migration sau khi tạo bảng (nếu đã có)


def save_reading(payload):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO readings (
            device_id, ts, lat, lon,
            co2_pct, flow_m3h, temp_c, pressure_atm, humidity_pct,
            so2_ppm, nox_ppm, co_ppm, o2_pct, pm_mgm3,
            zero_drift_pct, span_drift_pct, qaqc_status,
            last_rata_date, rata_accuracy_pct,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        payload.get('device_id'),
        payload.get('ts'),
        payload.get('location', {}).get('lat'),
        payload.get('location', {}).get('lon'),
        payload.get('co2_pct'),
        payload.get('flow_m3h'),
        payload.get('temp_c'),
        payload.get('pressure_atm'),
        payload.get('humidity_pct'),
        payload.get('so2_ppm'),
        payload.get('nox_ppm'),
        payload.get('co_ppm'),
        payload.get('o2_pct'),
        payload.get('pm_mgm3'),
        payload.get('zero_drift_pct'),
        payload.get('span_drift_pct'),
        payload.get('qaqc_status'),
        payload.get('last_rata_date'),
        payload.get('rata_accuracy_pct'),
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()


def send_to_server(payload):
    try:
        r = requests.post(SERVER_URL, json=payload, timeout=2)
        if r.status_code == 200:
            print(f"[HTTP] OK → {r.json()}")
        else:
            print(f"[HTTP] Server trả về {r.status_code}")
    except Exception as e:
        print(f"[HTTP] Lỗi: {e}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        dev = data.get('device_id')
        qaqc = data.get('qaqc_status', 'unknown')
        
        # Log đầy đủ hơn
        print(f"[RX] {dev} | Temp={data.get('temp_c')}C | Hum={data.get('humidity_pct')}% | "
              f"CO2={data.get('co2_pct'):.2f}% | SO2={data.get('so2_ppm'):.2f}ppm | "
              f"NOx={data.get('nox_ppm'):.2f}ppm | PM={data.get('pm_mgm3'):.2f}mg/m3 | "
              f"QA/QC={qaqc}")
        
        if qaqc == "out_of_control":
            print(f"[WARN] QA/QC out_of_control! zero_drift={data.get('zero_drift_pct'):.2f}% span_drift={data.get('span_drift_pct'):.2f}%")
        
        save_reading(data)
        send_to_server(data)
    except Exception as e:
        print(f"[ERROR] {e}")


def on_connect(client, userdata, flags, rc):
    print(f"[Gateway] Kết nối broker: {rc}")
    if rc == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"[Gateway] Subscribed to {MQTT_TOPIC}")
def modbus_poll_loop():
    """Poll adapter mỗi 10 giây và xử lý dữ liệu."""
    device = MockModbusDevice()
    while True:
        device.update_registers()
        data = device.read_sensor_data()
        # In log nhẹ
        print(f"[Modbus] CO2={data['co2_pct']:.2f}% | SO2={data['so2_ppm']:.2f}ppm | QA/QC={data['qaqc_status']}")
        # Lưu và forward giống như MQTT
        save_reading(data)
        send_to_server(data)
        time.sleep(10)

if __name__ == "__main__":
    init_db()
    if "--modbus" in sys.argv:
        print("Gateway v2 (Modbus mode) started. Polling every 10s...")
        modbus_poll_loop()
    else:
        client = mqtt.Client()
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        print("Gateway v2 started. Waiting for messages...")
        client.loop_forever()
