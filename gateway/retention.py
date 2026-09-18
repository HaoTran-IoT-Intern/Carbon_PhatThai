# gateway/retention.py
import shutil
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction


def evaluate_emergency_freeze(free_pct, current_state="NORMAL"):
    """
    Cơ chế Hysteresis:
    - Khi đĩa < 5%: Kích hoạt FREEZE
    - Chỉ phục hồi NORMAL khi đĩa > 8%
    """
    state = current_state.upper() if isinstance(current_state, str) else "NORMAL"
    if state == "FREEZE":
        if free_pct > 8.0:
            return "NORMAL"
        return "FREEZE"
    else:
        if free_pct < 5.0:
            return "FREEZE"
        return "NORMAL"


def check_disk_watermark(path="."):
    """
    Kiểm tra mức dung lượng đĩa:
    - CRITICAL: free < 5% hoặc free < 200MB
    - WARNING: free < 10% hoặc free < 1GB (1024MB)
    - OK: mức an toàn
    """
    total, used, free = shutil.disk_usage(path)
    if total <= 0:
        return "CRITICAL"
    percent_free = (free / total) * 100
    if percent_free < 5.0 or free < (200 * 1024 * 1024):
        return "CRITICAL"
    elif percent_free < 10.0 or free < (1024 * 1024 * 1024):
        return "WARNING"
    return "OK"


def purge_synced_data(conn, retention_days=30):
    """
    Purge raw_samples cho các phút đã synced và cũ hơn retention_days,
    với điều kiện has_unreconciled_late_data == 0.
    Tuyệt đối không purge dữ liệu unsynced, quarantined hoặc chưa đối soát muộn.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:00.000Z")
    with db_transaction(conn):
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM raw_samples 
            WHERE (stack_id, minute_window_ts) IN (
                SELECT stack_id, minute_ts FROM minute_readings 
                WHERE status = 'synced' AND minute_ts <= ? AND has_unreconciled_late_data = 0
            )
        """, (cutoff,))
        return cur.rowcount


class MosquittoServiceController:
    """Quản lý tiến trình dịch vụ Mosquitto trên Edge Gateway."""
    def __init__(self, command_executor=None):
        import subprocess
        self.command_executor = command_executor or subprocess.run

    def stop_service(self, service_name="mosquitto"):
        try:
            self.command_executor(["systemctl", "stop", service_name], check=False)
        except Exception as e:
            import logging
            logging.error(f"[SERVICE_CONTROLLER] Lỗi stop {service_name}: {e}")

    def start_service(self, service_name="mosquitto"):
        try:
            self.command_executor(["systemctl", "start", service_name], check=False)
        except Exception as e:
            import logging
            logging.error(f"[SERVICE_CONTROLLER] Lỗi start {service_name}: {e}")


def run_emergency_freeze_check(conn, disk_path=".", service_controller=None, log_file="/var/log/cems_emergency_freeze.log"):
    """
    Kiểm tra mức dung lượng đĩa và quản lý trạng thái DISK_EMERGENCY_FREEZE theo nguyên tắc Hysteresis.
    - < 5% hoặc < 200MB -> DISK_EMERGENCY_FREEZE, ngắt Mosquitto, ghi log
    - > 8% và > 500MB -> Phục hồi NORMAL, khởi động lại Mosquitto, ghi log
    """
    import os
    total, used, free = shutil.disk_usage(disk_path)
    free_pct = (free / total * 100) if total > 0 else 0.0

    cur = conn.cursor()
    cur.execute("SELECT value FROM gateway_system_state WHERE key = 'emergency_freeze_state'")
    row = cur.fetchone()
    current_state = "NORMAL"
    if row:
        try:
            current_state = row["value"]
        except (TypeError, IndexError):
            current_state = row[0]

    new_state = current_state
    if current_state in ("DISK_EMERGENCY_FREEZE", "FREEZE"):
        if free_pct > 8.0 and free > (500 * 1024 * 1024):
            new_state = "NORMAL"
        else:
            new_state = "DISK_EMERGENCY_FREEZE"
    else:
        if free_pct < 5.0 or free < (200 * 1024 * 1024):
            new_state = "DISK_EMERGENCY_FREEZE"
        else:
            new_state = "NORMAL"

    state_changed = (new_state != current_state)

    if state_changed:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if new_state == "DISK_EMERGENCY_FREEZE":
            log_msg = f"[{now_iso}] EMERGENCY_FREEZE ACTIVATED: Free disk {free_pct:.2f}%, free bytes {free}. Stopped Mosquitto broker.\n"
            if service_controller:
                service_controller.stop_service("mosquitto")
        else:
            log_msg = f"[{now_iso}] EMERGENCY_FREEZE CLEARED: Free disk {free_pct:.2f}%, free bytes {free}. Restored Mosquitto broker.\n"
            if service_controller:
                service_controller.start_service("mosquitto")

        # Ghi file log an toàn
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_msg)
        except Exception:
            try:
                local_fallback = "./logs/cems_emergency_freeze.log"
                os.makedirs(os.path.dirname(os.path.abspath(local_fallback)), exist_ok=True)
                with open(local_fallback, "a", encoding="utf-8") as f:
                    f.write(log_msg)
            except Exception:
                pass

        with db_transaction(conn):
            cur.execute("""
                INSERT INTO gateway_system_state (key, value, updated_at)
                VALUES ('emergency_freeze_state', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (new_state, now_iso))

    return {
        "status": new_state,
        "free_pct": free_pct,
        "free_bytes": free,
        "state_changed": state_changed
    }

