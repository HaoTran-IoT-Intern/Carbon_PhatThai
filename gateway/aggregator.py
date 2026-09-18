# gateway/aggregator.py
import json
import logging
from datetime import datetime, timezone, timedelta
from gateway.models import db_transaction

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def recompute_minute_rollup(conn, stack_id, minute_ts, config=None):
    config = config or {}
    now_str = get_iso_now()
    with db_transaction(conn):
        cur = conn.cursor()
        
        # Chỉ lấy các mẫu active (is_superseded = 0)
        cur.execute("""
            SELECT * FROM raw_samples 
            WHERE stack_id = ? AND minute_window_ts = ? AND is_superseded = 0
            ORDER BY slot_index ASC
        """, (stack_id, minute_ts))
        samples = [dict(r) for r in cur.fetchall()]
        
        received_count = len(samples)
        
        # 1. Thống kê theo slot & độ phủ CO2
        co2_valid_samples = []
        for s in samples:
            if all(s.get(k) is not None for k in ("co2_pct", "flue_flow_m3h", "flue_temp_c", "flue_pressure_abs_kpa")) and s.get("is_calibrating") == 0:
                co2_valid_samples.append(s)
                
        covered_seconds = len(co2_valid_samples) * 10
        if covered_seconds == 60:
            coverage_status = "full"
        elif covered_seconds > 0:
            coverage_status = "partial"
        else:
            coverage_status = "none"
            
        # 2. Thống kê đại lượng đo (avg, min, max)
        def calc_stat(keys):
            vals = [s[keys] for s in samples if s.get(keys) is not None]
            if not vals:
                return None
            return {"avg": round(sum(vals) / len(vals), 2), "min": round(min(vals), 2), "max": round(max(vals), 2), "data_source": "measured"}

        gases = {
            "co2_pct": calc_stat("co2_pct"),
            "so2_ppm": calc_stat("so2_ppm"),
            "nox_ppm": calc_stat("nox_ppm"),
            "co_ppm": calc_stat("co_ppm"),
            "o2_pct": calc_stat("o2_pct"),
            "pm_mgm3": calc_stat("pm_mgm3")
        }
        flue = {
            "temp_c": calc_stat("flue_temp_c"),
            "pressure_gauge_kpa": calc_stat("flue_pressure_gauge_kpa"),
            "pressure_abs_kpa": calc_stat("flue_pressure_abs_kpa"),
            "flow_actual_m3h": calc_stat("flue_flow_m3h")
        }
        ambient = {
            "temp_c": calc_stat("ambient_temp_c"),
            "humidity_pct": calc_stat("ambient_humidity_pct")
        }

        # 3. Tích phân CO2 trên co2_valid_samples
        if co2_valid_samples:
            co2_flows = [s["instantaneous_co2_flow_m3h"] for s in co2_valid_samples if s["instantaneous_co2_flow_m3h"] is not None]
            co2_vols = [s["instantaneous_co2_std_volume_m3"] for s in co2_valid_samples if s["instantaneous_co2_std_volume_m3"] is not None]
            integrated = {
                "method": "fixed_10s_rectangular_v1",
                "valid_samples": len(co2_valid_samples),
                "covered_seconds": covered_seconds,
                "coverage_status": coverage_status,
                "co2_flow_actual_m3h": {
                    "avg": round(sum(co2_flows) / len(co2_flows), 2) if co2_flows else None,
                    "min": round(min(co2_flows), 2) if co2_flows else None,
                    "max": round(max(co2_flows), 2) if co2_flows else None
                },
                "co2_standard_volume_observed_m3": round(sum(co2_vols), 2) if co2_vols else None
            }
        else:
            integrated = {
                "method": "fixed_10s_rectangular_v1",
                "valid_samples": 0,
                "covered_seconds": 0,
                "coverage_status": "none",
                "co2_flow_actual_m3h": {"avg": None, "min": None, "max": None},
                "co2_standard_volume_observed_m3": None
            }

        # 4. QA/QC Precedence độc lập với coverage
        qaqc_statuses = [s["qaqc_status"] for s in samples if s.get("qaqc_status")]
        if not qaqc_statuses or len(samples) == 0:
            m_qaqc = "unknown"
        elif all(q == "in_control" for q in qaqc_statuses):
            m_qaqc = "in_control"
        elif all(q == "out_of_control" for q in qaqc_statuses):
            m_qaqc = "out_of_control"
        else:
            m_qaqc = "mixed"

        device_id = samples[0]["device_id"] if samples else "unknown"
        config_id = config.get("config_id", (samples[0]["measurement_config_id"] if samples and samples[0].get("measurement_config_id") else "cfg_cement_stack_v1"))
        
        cur.execute("""
            INSERT INTO minute_readings (
                stack_id, device_id, measurement_config_id, minute_ts, window_seconds, gap_reason,
                expected_samples, received_samples, valid_samples, covered_seconds,
                coverage_status, valid_by_metric_json, excluded_by_reason_json,
                gases_and_dust_json, flue_gas_json, ambient_json, integrated_emissions_json,
                minute_qaqc_status, qaqc_checks_json, status, created_at
            ) VALUES (?, ?, ?, ?, 60, NULL, 6, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            ON CONFLICT(stack_id, minute_ts) DO UPDATE SET
                device_id = CASE WHEN excluded.device_id != 'unknown' THEN excluded.device_id ELSE minute_readings.device_id END,
                measurement_config_id = excluded.measurement_config_id,
                received_samples = excluded.received_samples,
                valid_samples = excluded.valid_samples,
                covered_seconds = excluded.covered_seconds,
                coverage_status = excluded.coverage_status,
                valid_by_metric_json = excluded.valid_by_metric_json,
                excluded_by_reason_json = excluded.excluded_by_reason_json,
                gases_and_dust_json = excluded.gases_and_dust_json,
                flue_gas_json = excluded.flue_gas_json,
                ambient_json = excluded.ambient_json,
                integrated_emissions_json = excluded.integrated_emissions_json,
                minute_qaqc_status = excluded.minute_qaqc_status
        """, (
            stack_id, device_id, config_id, minute_ts,
            received_count, len(co2_valid_samples), covered_seconds, coverage_status,
            json.dumps({"co2_pct": len(co2_valid_samples)}),
            json.dumps({"sensor_fault_samples": received_count - len(co2_valid_samples)}),
            json.dumps(gases), json.dumps(flue), json.dumps(ambient), json.dumps(integrated),
            m_qaqc, json.dumps({"zero_drift_pct": 0.85, "span_drift_pct": 1.2}), now_str
        ))

def startup_gap_backfill(conn, config, current_time_dt=None):
    now_dt = current_time_dt or datetime.now(timezone.utc)
    total_backfilled = 0
    
    with db_transaction(conn):
        cur = conn.cursor()
        for stack in config.get("operational_stacks", []):
            if not stack.get("is_active"):
                continue
            stack_id = stack["stack_id"]
            
            cur.execute("SELECT max(minute_ts) as last_ts FROM minute_readings WHERE stack_id = ?", (stack_id,))
            row = cur.fetchone()
            last_ts = None
            if row:
                try:
                    last_ts = row["last_ts"]
                except (TypeError, IndexError):
                    last_ts = row[0]

            if last_ts:
                start_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00")) + timedelta(minutes=1)
            elif stack.get("active_from"):
                start_dt = datetime.fromisoformat(stack["active_from"].replace("Z", "+00:00"))
            else:
                start_dt = now_dt - timedelta(hours=24)
                
            active_until_dt = None
            if stack.get("active_until"):
                active_until_dt = datetime.fromisoformat(stack["active_until"].replace("Z", "+00:00"))
                
            effective_end_dt = min(now_dt, active_until_dt) if active_until_dt else now_dt
            max_backfill_dt = effective_end_dt - timedelta(hours=24)
            
            if start_dt < max_backfill_dt:
                logging.warning(f"[GAP_BACKFILL] Gap vượt quá 24h ({start_dt} -> {effective_end_dt}). Giới hạn bù đắp 24h.")
                start_dt = max_backfill_dt
                
            start_dt = start_dt.replace(second=0, microsecond=0)
            cutoff_dt = effective_end_dt - timedelta(seconds=120)
            
            curr_dt = start_dt
            while curr_dt <= cutoff_dt:
                minute_ts_str = curr_dt.strftime("%Y-%m-%dT%H:%M:00.000Z")
                cur.execute("""
                    INSERT OR IGNORE INTO minute_readings (
                        stack_id, device_id, measurement_config_id, minute_ts, window_seconds, gap_reason,
                        expected_samples, received_samples, valid_samples, covered_seconds,
                        coverage_status, valid_by_metric_json, excluded_by_reason_json,
                        minute_qaqc_status, qaqc_checks_json, status, created_at
                    ) VALUES (?, ?, ?, ?, 60, 'gateway_offline_backfill', 6, 0, 0, 0, 'none', '{}', '{}', 'unknown', '{}', 'draft', ?)
                """, (stack_id, stack["device_id"], config.get("config_id", "cfg_cement_stack_v1"), minute_ts_str, get_iso_now()))
                if cur.rowcount > 0:
                    total_backfilled += 1
                curr_dt += timedelta(minutes=1)
                
    return total_backfilled
