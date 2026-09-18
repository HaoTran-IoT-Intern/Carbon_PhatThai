import random
import time

class MockModbusDevice:
    """Giả lập thiết bị Modbus TCP với các thanh ghi chứa giá trị cảm biến."""
    def __init__(self, device_id="modbus_node_01"):
        self.device_id = device_id
        self.lat = 21.0278
        self.lon = 105.8342
        # 14 giá trị sensor
        self.co2_pct = 15.0
        self.so2_ppm = 85.0
        self.nox_ppm = 120.0
        self.co_ppm = 35.0
        self.o2_pct = 18.0
        self.pm_mgm3 = 12.0
        self.flow_m3h = 2150.0
        self.temp_c = 28.0
        self.humidity_pct = 60.0
        self.pressure_atm = 1.2
        self.zero_drift_pct = 0.0
        self.span_drift_pct = 0.0
        self.qaqc_status = "in_control"
        self.last_rata_date = "2026-08-20"
        self.rata_accuracy_pct = 1.5

    def _mock_sensor(self, base, range_):
        return base + random.uniform(-range_, range_)

    def update_registers(self):
        """Cập nhật tất cả giá trị sensor với nhiễu ngẫu nhiên."""
        self.co2_pct = self._mock_sensor(15.0, 2.0)
        self.so2_ppm = self._mock_sensor(85.0, 15.0)
        self.nox_ppm = self._mock_sensor(120.0, 30.0)
        self.co_ppm = self._mock_sensor(35.0, 8.0)
        self.o2_pct = self._mock_sensor(18.0, 3.0)
        self.pm_mgm3 = self._mock_sensor(12.0, 5.0)
        self.flow_m3h = self._mock_sensor(2150.0, 300.0)
        self.temp_c = self._mock_sensor(28.0, 3.0)
        self.humidity_pct = self._mock_sensor(60.0, 5.0)
        self.pressure_atm = self._mock_sensor(1.2, 0.1)
        # QA/QC
        self.zero_drift_pct = random.uniform(-2.0, 2.0)
        self.span_drift_pct = random.uniform(-3.0, 3.0)
        self.qaqc_status = "in_control" if (abs(self.zero_drift_pct) < 2.0 and abs(self.span_drift_pct) < 2.0) else "out_of_control"
        # RATA
        self.last_rata_date = "2026-08-20"
        self.rata_accuracy_pct = 1.5

    def read_sensor_data(self):
        """Trả về dict dữ liệu giống như payload của firmware."""
        return {
            "device_id": self.device_id,
            "ts": f"modbus_{int(time.time())}",
            "location": {"lat": self.lat, "lon": self.lon},
            "co2_pct": round(self.co2_pct, 2),
            "so2_ppm": round(self.so2_ppm, 2),
            "nox_ppm": round(self.nox_ppm, 2),
            "co_ppm": round(self.co_ppm, 2),
            "o2_pct": round(self.o2_pct, 2),
            "pm_mgm3": round(self.pm_mgm3, 2),
            "flow_m3h": round(self.flow_m3h, 2),
            "temp_c": round(self.temp_c, 2),
            "humidity_pct": round(self.humidity_pct, 2),
            "pressure_atm": round(self.pressure_atm, 2),
            "zero_drift_pct": round(self.zero_drift_pct, 2),
            "span_drift_pct": round(self.span_drift_pct, 2),
            "qaqc_status": self.qaqc_status,
            "last_rata_date": self.last_rata_date,
            "rata_accuracy_pct": self.rata_accuracy_pct
        }
