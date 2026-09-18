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
