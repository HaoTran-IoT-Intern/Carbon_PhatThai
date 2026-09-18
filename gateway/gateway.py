#!/usr/bin/env python3
"""
CEMS Edge Gateway — Production Daemon (Hướng 1 - Bên phát thải)
Tuân thủ Đặc tả Kỹ thuật Spec v1.2.3:
- Ingest mẫu thô 10s từ Local Mosquitto Broker (Topic: pione/carbon/v1/+/data)
- Quản lý khe thời gian & giải quyết xung đột mẫu thô (Slot Manager)
- Động cơ tổng hợp phút & tích phân CO2 cơ sở khí ướt (Aggregator)
- Bù đắp phút rỗng khi khởi động lại sau sự cố mất điện (Startup Gap Backfill)
- Đóng băng lô 5-phút bất biến (Batch Manager)
- Động cơ đẩy lô đồng bộ lên Cloud Backend (Dispatcher)
- Giám sát đĩa & Đóng băng khẩn cấp Hysteresis (Retention & Emergency Freeze)
"""

import os
import sys

# Đảm bảo PROJECT_ROOT nằm ở đầu sys.path và gỡ bỏ SCRIPT_DIR để tránh che khuất package gateway
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
while SCRIPT_DIR in sys.path:
    sys.path.remove(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import time
import json
import signal
import logging
import threading
from datetime import datetime, timezone, timedelta
import paho.mqtt.client as mqtt
import requests

from gateway.models import init_db, get_db_connection, load_config
from gateway.slot_manager import ingest_raw_sample
from gateway.aggregator import recompute_minute_rollup, startup_gap_backfill
from gateway.batch_manager import freeze_and_create_batch
from gateway.dispatcher import dispatch_pending_batches
from gateway.retention import run_emergency_freeze_check, purge_synced_data, MosquittoServiceController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Gateway] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)


class GatewayDaemon:
    def __init__(self, config=None, config_path=None):
        if config is not None:
            self.config = config
        else:
            cfg_file = config_path or "gateway/config.json"
            if os.path.exists(cfg_file):
                self.config = load_config(cfg_file)
            else:
                self.config = {
                    "gateway_id": "gw_cement_01",
                    "database_path": "cems_data.db",
                    "api_endpoint": "http://127.0.0.1:5001/api/v1/cems/telemetry/batch",
                    "api_key": "cems_gw_secret_key_prod_xxxxxxxxx",
                    "mqtt_broker": {
                        "host": "127.0.0.1",
                        "port": 1883,
                        "username": "gateway",
                        "password": "cems_gw_secret_pass"
                    },
                    "operational_stacks": [
                        {
                            "stack_id": "stack_01",
                            "device_id": "cems_node_01",
                            "is_active": True,
                            "active_from": "2026-09-18T00:00:00.000Z"
                        }
                    ],
                    "flue_gas_sensors": {
                        "barometric_pressure_kpa_default": 101.325,
                        "pressure_gauge_valid_range_kpa": [-50.0, 50.0]
                    }
                }

        self.gateway_id = self.config.get("gateway_id", "gw_01")
        self.db_path = self.config.get("database_path", "cems_data.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)

        # 1. Thực hiện startup gap backfill khi khởi động
        logging.info("Khởi động Gateway Daemon. Đang kiểm tra bù đắp phút rỗng (Startup Gap Backfill)...")
        backfilled = startup_gap_backfill(self.conn, self.config)
        if backfilled > 0:
            logging.info(f"Đã bù đắp thành công {backfilled} phút rỗng khi khởi động.")

        # 2. Khởi tạo controller dịch vụ và HTTP session
        self.service_controller = MosquittoServiceController()
        self.http_session = requests.Session()
        self.is_running = False
        self.worker_thread = None
        self.lock = threading.Lock()

        # 3. Cấu hình Paho MQTT client
        client_id = f"gateway_{self.gateway_id}_{os.getpid()}"
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:
            self.mqtt_client = mqtt.Client(client_id=client_id)
        mqtt_cfg = self.config.get("mqtt_broker", {})
        if mqtt_cfg.get("username"):
            self.mqtt_client.username_pw_set(mqtt_cfg.get("username"), mqtt_cfg.get("password"))
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

    def on_mqtt_connect(self, client, userdata, flags, rc, *args, **kwargs):
        rc_code = rc if isinstance(rc, int) else getattr(rc, "value", 0)
        if rc_code == 0:
            topic = "pione/carbon/v1/+/data"
            client.subscribe(topic)
            logging.info(f"Đã kết nối Mosquitto Broker thành công (rc=0). Subscribed topic: {topic}")
        else:
            logging.error(f"Kết nối Mosquitto Broker thất bại (rc={rc})")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload_str = msg.payload.decode("utf-8")
            raw_data = json.loads(payload_str)
            raw_data["raw_payload_text"] = payload_str
            if "stack_id" not in raw_data:
                # Default to first stack if omitted
                raw_data["stack_id"] = self.config.get("operational_stacks", [{}])[0].get("stack_id", "stack_01")
            if "measurement_config_id" not in raw_data:
                raw_data["measurement_config_id"] = self.config.get("config_id", "cfg_cement_stack_v1")

            with self.lock:
                res = ingest_raw_sample(self.conn, raw_data, self.config)
            
            logging.info(f"[RX] Mẫu thô từ {raw_data.get('device_id')}: seq={raw_data.get('sample_seq')} "
                         f"time_quality={raw_data.get('time_quality')} -> {res.get('status')}")
            return res.get("status")
        except Exception as e:
            logging.error(f"[RX_ERROR] Lỗi phân tích payload MQTT: {e}")
            return "error"

    def run_aggregation_cycle(self, minute_ts_list=None):
        """Tính toán tổng hợp rollup phút cho các stack đang hoạt động."""
        with self.lock:
            now_dt = datetime.now(timezone.utc)
            if minute_ts_list:
                targets = minute_ts_list
            else:
                # Mặc định tổng hợp phút hiện tại và phút vừa qua
                m0 = now_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
                m1 = (now_dt - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00.000Z")
                targets = [m1, m0]

            for stack in self.config.get("operational_stacks", []):
                if not stack.get("is_active"):
                    continue
                stack_id = stack["stack_id"]
                for m_ts in targets:
                    recompute_minute_rollup(self.conn, stack_id, m_ts, self.config)

    def run_batch_and_dispatch_cycle(self, session=None, current_time_dt=None):
        """Đóng băng các phút hợp lệ thành lô và phát tán đồng bộ lên Cloud."""
        session = session or self.http_session
        dispatched_count = 0
        with self.lock:
            # 1. Đóng băng các phút quá grace period vào batch
            for stack in self.config.get("operational_stacks", []):
                if not stack.get("is_active"):
                    continue
                stack_id = stack["stack_id"]
                while True:
                    batch_id = freeze_and_create_batch(
                        self.conn, stack_id, self.gateway_id, limit=5, current_time_dt=current_time_dt
                    )
                    if not batch_id:
                        break
                    logging.info(f"[BATCH] Đã đóng băng lô mới: {batch_id} cho stack {stack_id}")

            # 2. Đẩy các batch đang chờ (pending) lên Backend
            dispatch_results = dispatch_pending_batches(self.conn, self.config, session=session, max_batches=10)
            for res in dispatch_results:
                if res.get("status") == "synced":
                    dispatched_count += 1

        return {"dispatched_count": dispatched_count}

    def run_maintenance_cycle(self):
        """Kiểm tra đĩa khẩn cấp và purge định kỳ."""
        with self.lock:
            freeze_res = run_emergency_freeze_check(
                self.conn, disk_path=".", service_controller=self.service_controller
            )
            if freeze_res.get("state_changed"):
                logging.warning(f"[MAINTENANCE] Trạng thái Emergency Freeze thay đổi: {freeze_res.get('status')}")

            purge_synced_data(self.conn, retention_days=30)

    def worker_loop(self):
        """Vòng lặp tiến trình nền của Gateway."""
        last_aggregation = 0
        last_batch_dispatch = 0
        last_maintenance = 0

        while self.is_running:
            now = time.time()
            # 1. Mỗi 10 giây: Tổng hợp rollup phút
            if now - last_aggregation >= 10:
                self.run_aggregation_cycle()
                last_aggregation = now

            # 2. Mỗi 30 giây: Đóng băng & Dispatch lô
            if now - last_batch_dispatch >= 30:
                self.run_batch_and_dispatch_cycle()
                last_batch_dispatch = now

            # 3. Mỗi 60 giây: Kiểm tra đĩa & bảo trì
            if now - last_maintenance >= 60:
                self.run_maintenance_cycle()
                last_maintenance = now

            time.sleep(1)

    def start(self):
        """Bắt đầu chạy daemon."""
        self.is_running = True
        mqtt_cfg = self.config.get("mqtt_broker", {})
        host = mqtt_cfg.get("host", "127.0.0.1")
        port = mqtt_cfg.get("port", 1883)
        try:
            self.mqtt_client.connect(host, port, 60)
            self.mqtt_client.loop_start()
            logging.info(f"Đã kích hoạt MQTT loop trên {host}:{port}")
        except Exception as e:
            logging.error(f"Không thể kết nối MQTT Broker trên {host}:{port}: {e}")

        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()
        logging.info("Gateway Daemon đã khởi động hoàn tất và đang hoạt động.")

    def shutdown(self):
        """Dừng daemon an toàn."""
        logging.info("Đang dừng Gateway Daemon...")
        self.is_running = False
        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)
        if self.conn:
            self.conn.close()
        logging.info("Gateway Daemon đã tắt hoàn toàn.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CEMS Edge Gateway Production Daemon v1.2.3")
    parser.add_argument("--config", default="gateway/config.json", help="Path to configuration file")
    parser.add_argument("--once", action="store_true", help="Chạy một chu kỳ duy nhất rồi thoát (thử nghiệm/cron)")
    parser.add_argument("--modbus", action="store_true", help="Chạy chế độ Modbus Adapter")
    args = parser.parse_args()

    daemon = GatewayDaemon(config_path=args.config)

    def sig_handler(signum, frame):
        logging.info(f"Nhận tín hiệu kết thúc ({signum}). Đang tắt...")
        daemon.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    if args.once:
        logging.info("Chạy chế độ 1-cycle (--once)...")
        daemon.run_aggregation_cycle()
        daemon.run_batch_and_dispatch_cycle()
        daemon.run_maintenance_cycle()
        daemon.shutdown()
        logging.info("Hoàn thành chu kỳ --once.")
        return

    daemon.start()
    while daemon.is_running:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break
    daemon.shutdown()


if __name__ == "__main__":
    main()
