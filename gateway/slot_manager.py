# gateway/slot_manager.py
import json
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def _to_dict(s):
    if isinstance(s, dict):
        return s
    return dict(s)

def compare_slot_priority(sample_a, sample_b):
    """
    Chuỗi ưu tiên kỹ thuật:
    1. time_quality (ntp_synced > gateway_assigned > invalid)
    2. Độ đầy đủ bộ tứ cốt lõi (co2, flow, temp, pressure_gauge)
    3. Trạng thái hiệu chuẩn (vận hành bình thường 0 > đang hiệu chuẩn 1)
    4. arrived_at (đến sớm hơn thắng)
    """
    dict_a = _to_dict(sample_a)
    dict_b = _to_dict(sample_b)

    tq_rank = {"ntp_synced": 3, "gateway_assigned": 2, "invalid": 1}
    a_tq = tq_rank.get(dict_a.get("time_quality"), 0)
    b_tq = tq_rank.get(dict_b.get("time_quality"), 0)
    if a_tq != b_tq:
        return 1 if a_tq > b_tq else -1
        
    def has_core(s):
        return all(s.get(k) is not None for k in ("co2_pct", "flue_flow_m3h", "flue_temp_c", "flue_pressure_gauge_kpa"))
    if has_core(dict_a) != has_core(dict_b):
        return 1 if has_core(dict_a) else -1
        
    a_cal = dict_a.get("is_calibrating", 0) or 0
    b_cal = dict_b.get("is_calibrating", 0) or 0
    if a_cal != b_cal:
        return 1 if a_cal == 0 else -1
        
    a_arr = dict_a.get("arrived_at", "") or ""
    b_arr = dict_b.get("arrived_at", "") or ""
    if a_arr != b_arr:
        return 1 if a_arr < b_arr else -1
    return 0

def ingest_raw_sample(conn, raw_data, config=None):
    config = config or {}
    now_str = get_iso_now()
    arrived_at_str = raw_data.get("arrived_at") or now_str
    
    with db_transaction(conn):
        cur = conn.cursor()
        
        # 1. Kiểm tra duplicate raw
        cur.execute("""
            SELECT id, raw_payload_text FROM raw_samples 
            WHERE device_id = ? AND boot_id = ? AND sample_seq = ?
        """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"]))
        existing = cur.fetchone()
        
        if existing:
            if existing["raw_payload_text"] == raw_data.get("raw_payload_text", ""):
                return {"status": "duplicate_ignored"}
            else:
                cur.execute("""
                    INSERT INTO raw_ingest_conflicts (
                        device_id, boot_id, sample_seq, existing_payload, conflicting_payload, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"], 
                      existing["raw_payload_text"], raw_data.get("raw_payload_text", ""), now_str))
                return {"status": "conflict_rejected"}

        # 2. Xử lý mẫu có time_quality == 'invalid' hoặc sample_ts lỗi
        is_invalid_time = (raw_data.get("time_quality") == "invalid")
        dt_sample = None
        if not is_invalid_time:
            try:
                sample_ts_str = raw_data.get("sample_ts", "")
                if not sample_ts_str:
                    is_invalid_time = True
                else:
                    dt_sample = datetime.fromisoformat(sample_ts_str.replace("Z", "+00:00"))
                    if dt_sample.tzinfo is None:
                        dt_sample = dt_sample.replace(tzinfo=timezone.utc)
                    dt_sample = dt_sample.astimezone(timezone.utc)
            except Exception:
                is_invalid_time = True
                
        if is_invalid_time:
            cur.execute("""
                INSERT INTO raw_samples (
                    device_id, boot_id, sample_seq, stack_id, measurement_config_id,
                    sample_ts, time_quality, arrived_at, minute_window_ts, slot_index,
                    is_superseded, is_late, metric_quality_json, qaqc_status, raw_payload_text, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, 'invalid', ?, NULL, NULL, 1, 3, '{}', 'unknown', ?, ?)
            """, (raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"],
                  raw_data["stack_id"], raw_data["measurement_config_id"], arrived_at_str,
                  raw_data.get("raw_payload_text", ""), now_str))
            return {"status": "accepted_invalid_time"}

        # 3. Phân bổ slot và minute_window_ts
        slot_k = int(dt_sample.second // 10)
        minute_window_ts = dt_sample.strftime("%Y-%m-%dT%H:%M:00.000Z")
        window_end_dt = dt_sample.replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        arrived_dt = datetime.fromisoformat(arrived_at_str.replace("Z", "+00:00"))
        if arrived_dt.tzinfo is None:
            arrived_dt = arrived_dt.replace(tzinfo=timezone.utc)
        arrived_dt = arrived_dt.astimezone(timezone.utc)
        
        # 4. Kiểm tra trạng thái đóng băng của phút
        cur.execute("SELECT id, status FROM minute_readings WHERE stack_id = ? AND minute_ts = ?",
                    (raw_data["stack_id"], minute_window_ts))
        m_row = cur.fetchone()
        
        is_superseded = 0
        is_late = 0
        
        if m_row and m_row["status"] in ("batched", "synced", "quarantined"):
            # Phút đã đóng băng -> Bất biến!
            is_superseded = 1
            is_late = 2
            cur.execute("UPDATE minute_readings SET has_unreconciled_late_data = 1 WHERE id = ?", (m_row["id"],))
            status_result = "accepted_late_frozen"
        else:
            # Xác định is_late
            if arrived_dt <= window_end_dt:
                is_late = 0
            elif arrived_dt <= window_end_dt + timedelta(seconds=60):
                is_late = 1
            else:
                is_late = 2
                is_superseded = 1

            # 5. Slot collision resolution nếu chưa bị supersede
            if is_superseded == 0:
                cur.execute("""
                    SELECT id, time_quality, is_calibrating, arrived_at, co2_pct, flue_flow_m3h, flue_temp_c, flue_pressure_gauge_kpa
                    FROM raw_samples 
                    WHERE stack_id = ? AND minute_window_ts = ? AND slot_index = ? AND is_superseded = 0
                """, (raw_data["stack_id"], minute_window_ts, slot_k))
                current_active = cur.fetchone()
                if current_active:
                    active_dict = dict(current_active)
                    if compare_slot_priority(raw_data, active_dict) > 0:
                        cur.execute("UPDATE raw_samples SET is_superseded = 1 WHERE id = ?", (active_dict["id"],))
                    else:
                        is_superseded = 1
            status_result = "accepted"

        # 6. Quy đổi áp suất và thể tích tức thời (cơ sở khí ướt)
        co2_val = raw_data.get("co2_pct")
        flow_val = raw_data.get("flue_flow_m3h")
        temp_val = raw_data.get("flue_temp_c")
        press_gauge_val = raw_data.get("flue_pressure_gauge_kpa")
        
        press_abs_val = None
        inst_flow = None
        inst_vol = None
        
        baro_kpa = config.get("flue_gas_sensors", {}).get("barometric_pressure_kpa_default", 101.325)
        valid_range = config.get("flue_gas_sensors", {}).get("pressure_gauge_valid_range_kpa", [-50.0, 50.0])
        std_p = config.get("standard_conditions", {}).get("pressure_kpa", 101.325)
        std_t = config.get("standard_conditions", {}).get("temperature_k", 273.15)
        
        if press_gauge_val is not None and (valid_range[0] <= press_gauge_val <= valid_range[1]):
            press_abs_val = round(press_gauge_val + baro_kpa, 3)
            
        if all(v is not None for v in (co2_val, flow_val, temp_val, press_abs_val)):
            inst_flow = flow_val * (co2_val / 100.0)
            t_k = temp_val + 273.15
            if t_k > 0 and std_p > 0:
                std_factor = (std_t / t_k) * (press_abs_val / std_p)
                inst_vol = inst_flow * std_factor * (10.0 / 3600.0)

        mq = raw_data.get("metric_quality") or raw_data.get("metric_quality_json") or {}
        mq_str = mq if isinstance(mq, str) else json.dumps(mq)

        cur.execute("""
            INSERT INTO raw_samples (
                device_id, boot_id, sample_seq, stack_id, measurement_config_id,
                sample_ts, time_quality, arrived_at, minute_window_ts, slot_index,
                is_superseded, is_late, co2_pct, so2_ppm, nox_ppm, co_ppm, o2_pct, pm_mgm3,
                flue_temp_c, flue_pressure_gauge_kpa, flue_pressure_abs_kpa, flue_flow_m3h,
                ambient_temp_c, ambient_humidity_pct, instantaneous_co2_flow_m3h, instantaneous_co2_std_volume_m3,
                metric_quality_json, qaqc_status, is_calibrating, raw_payload_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            raw_data["device_id"], raw_data["boot_id"], raw_data["sample_seq"], raw_data["stack_id"],
            raw_data["measurement_config_id"], raw_data.get("sample_ts"), raw_data.get("time_quality", "gateway_assigned"),
            arrived_at_str, minute_window_ts, slot_k,
            is_superseded, is_late, co2_val, raw_data.get("so2_ppm"),
            raw_data.get("nox_ppm"), raw_data.get("co_ppm"), raw_data.get("o2_pct"), raw_data.get("pm_mgm3"),
            temp_val, press_gauge_val, press_abs_val, flow_val,
            raw_data.get("ambient_temp_c"), raw_data.get("ambient_humidity_pct"),
            inst_flow, inst_vol, mq_str,
            raw_data.get("qaqc_status", "in_control"), raw_data.get("is_calibrating", 0),
            raw_data.get("raw_payload_text", ""), now_str
        ))
        
        return {"status": status_result}
