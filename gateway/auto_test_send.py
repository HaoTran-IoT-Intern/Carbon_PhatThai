import requests
import time
from datetime import datetime

# --- CẤU HÌNH ---
# Địa chỉ server đang chạy (localhost vì server và script chạy trên cùng 1 máy)
SERVER_URL = "http://localhost:5001/receive"

# Thời gian chờ giữa các lần gửi (giây). 
# ESP32 publish mỗi 10s, nên ta dùng 10s để mô phỏng đúng thực tế.
INTERVAL_SECONDS = 10

def get_test_payload():
    """
    Tạo payload JSON giống dữ liệu gửi từ ESP32.
    - ts: timestamp hiện tại theo múi giờ Việt Nam (+0700)
    - Giá trị số nhỏ biến đổi nhẹ để mô phỏng dữ liệu thực.
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0700")
    
    # Tạo giá trị số nhẹ thay đổi theo thời gian để nhìn thấy dữ liệu biến thiên
    # (túy chọn: có thể dùng giá trị cố định nếu chỉ test sự ổn định)
    co2_val = round(10.0 + 2.0 * (time.time() % 1), 2)  # 9.XX - 11.XX
    flow_val = round(2150.0 + 50.0 * (time.time() % 1), 2) # 21XX
    temp_val = round(150.0 + 5.0 * (time.time() % 1), 1) # 149.X - 151.X
    pressure_val = round(1.2 + 0.2 * (time.time() % 1), 2) # 1.0 - 1.4

    payload = {
        "device_id": "cems_node_01",
        "ts": now,
        "location": {"lat": 21.0278, "lon": 105.8342},
        "co2_pct": co2_val,
        "flow_m3h": flow_val,
        "temp_c": temp_val,
        "pressure_atm": pressure_val
    }
    return payload

def send_payload(payload):
    """Gửi payload lên server và in kết quả."""
    try:
        response = requests.post(SERVER_URL, json=payload, timeout=5)
        # In ra dòng log ngắn gọn
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sent -> Status: {response.status_code} (OK)")
        return response.status_code
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Lỗi kết nối: {e}")
        return None

if __name__ == "__main__":
    print("=== BẮT ĐỘC AUTO TEST SEND ===")
    print(f"Server: {SERVER_URL}")
    print(f"Mỗi {INTERVAL_SECONDS}s gửi 1 bản tin CEMS.")
    print("Nhấn Ctrl+C để dừng script.\n")
    
    try:
        while True:
            # 1. Tạo payload
            payload = get_test_payload()
            
            # 2. Gửi payload
            send_payload(payload)
            
            # 3. Chờ INTERVAL_SECONDS trước khi gửi lần tiếp theo
            time.sleep(INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        print("\n=== ĐÃ DỪNG SCRIPT ===")

