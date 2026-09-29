from datetime import datetime, timezone
import json

def get_iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def parse_json_field(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        parsed = val
    else:
        try:
            parsed = json.loads(val)
        except Exception:
            return default
    if not isinstance(parsed, type(default)):
        return default
    return parsed

DEFAULT_GASES = {
    "co2_pct": {"avg": None, "min": None, "max": None, "data_source": "none"},
    "so2_ppm": {"avg": None, "min": None, "max": None, "data_source": "none"},
    "nox_ppm": {"avg": None, "min": None, "max": None, "data_source": "none"},
    "co_ppm":  {"avg": None, "min": None, "max": None, "data_source": "none"},
    "o2_pct":  {"avg": None, "min": None, "max": None, "data_source": "none"},
    "pm_mgm3": {"avg": None, "min": None, "max": None, "data_source": "none"}
}

DEFAULT_FLUE = {
    "temp_c": {"avg": None, "min": None, "max": None, "data_source": "none"},
    "pressure_abs_kpa": {"avg": None, "min": None, "max": None, "pressure_type": "absolute", "data_source": "none"},
    "flow_actual_m3h": {"avg": None, "min": None, "max": None, "data_source": "none"}
}

DEFAULT_AMBIENT = {
    "temp_c": {"avg": None, "min": None, "max": None, "data_source": "none"},
    "humidity_pct": {"avg": None, "min": None, "max": None, "data_source": "none"}
}

DEFAULT_VALID_BY_METRIC = {
    "co2_pct": 0, "so2_ppm": 0, "nox_ppm": 0, "co_ppm": 0, "o2_pct": 0, "pm_mgm3": 0,
    "flue_temp_c": 0, "flue_pressure_abs_kpa": 0, "flue_flow_m3h": 0, "ambient_temp_c": 0, "ambient_humidity_pct": 0
}

DEFAULT_EXCLUDED_BY_REASON = {
    "sensor_fault_samples": 0, "calibrating_samples": 0, "qaqc_failed_samples": 0
}
