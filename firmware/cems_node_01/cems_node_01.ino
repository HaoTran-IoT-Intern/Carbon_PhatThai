/*
 * ESP32 CEMS Node - Hướng 1 (Bên phát thải) - v2.1
 * 6 khí+bụi, 3 vật lý (DHT11 temp + humidity, flow, flue_pressure_gauge_kpa), QA/QC mock
 * LCD 16x2 I2C, Buzzer alert, Buffer store-and-forward (20 samples ~200s limit)
 * Local Mosquitto Broker with authentication & Buffer full Red LED indicator
 */

#include <WiFi.h>
#include <DHT.h>
#include "config.h"
#define MQTT_MAX_PACKET_SIZE 1024
#include <PubSubClient.h>
#include <time.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define DHTPIN 4
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

#define BUZZER_PIN 18
#define LED_BUFFER_FULL_PIN 23

// I2C pins for standard ESP32 WROOM-32 (Default: SDA=GPIO21, SCL=GPIO22)
#define I2C_SDA 21
#define I2C_SCL 22

LiquidCrystal_I2C lcd(0x27, 16, 2);
bool lcd_found = false;

#define WINDOW_SIZE 5
#define BUFFER_SIZE 20
#define MAX_PAYLOAD_LEN 768

// WiFi Credentials from config.h with defaults
const char* WIFI_SSID = DEFAULT_WIFI_SSID;
const char* WIFI_PASS = DEFAULT_WIFI_PASS;
const unsigned long WIFI_TIMEOUT_MS = DEFAULT_WIFI_TIMEOUT_MS;

// MQTT Broker Configuration (Local Edge Gateway Mosquitto)
const char* MQTT_HOST = DEFAULT_MQTT_HOST;
const uint16_t MQTT_PORT = DEFAULT_MQTT_PORT;
const char* MQTT_USER = DEFAULT_MQTT_USER;
const char* MQTT_PASS = DEFAULT_MQTT_PASS;
const char* MQTT_TOPIC = DEFAULT_MQTT_TOPIC;

// Node & Stack Identity
const char* DEVICE_ID = DEFAULT_DEVICE_ID;
const char* STACK_ID = DEFAULT_STACK_ID;
const char* MEASUREMENT_CONFIG_ID = DEFAULT_MEASUREMENT_CONFIG_ID;
const float LAT = DEFAULT_NODE_LAT;
const float LON = DEFAULT_NODE_LON;

// Runtime state
char boot_id[37] = "";
uint32_t sample_seq = 0;

// LED
const int LED_PIN = 2; // Normal status LED

// Timing
const unsigned long PUBLISH_INTERVAL_MS = 10000;
unsigned long last_publish_ms = 0;

// State
bool wifi_ok = false;
bool mqtt_ok = false;
bool simulated_outage = false;

WiFiClient wifi_client;
PubSubClient mqtt(wifi_client);

// ---- History buffers for Moving Average (MA-5) ----
float co2_history[WINDOW_SIZE];
float so2_history[WINDOW_SIZE];
float nox_history[WINDOW_SIZE];
float co_history[WINDOW_SIZE];
float o2_history[WINDOW_SIZE];
float pm_history[WINDOW_SIZE];
float flow_history[WINDOW_SIZE];
float temp_history[WINDOW_SIZE];
float flue_pressure_gauge_history[WINDOW_SIZE];
float humidity_history[WINDOW_SIZE];
int sample_index = 0;

// Buffer
char payload_buffer[BUFFER_SIZE][MAX_PAYLOAD_LEN];
int buffer_head = 0, buffer_tail = 0, buffer_count = 0;

// --- Sensor data structure ---
struct SensorData {
  // Định danh & chất lượng
  const char* boot_id;
  uint32_t sample_seq;
  const char* stack_id;
  const char* measurement_config_id;
  const char* time_quality;
  int is_calibrating;

  // 6 khí + bụi
  float co2_pct;
  float so2_ppm;
  float nox_ppm;
  float co_ppm;
  float o2_pct;
  float pm_mgm3;

  // QA/QC
  float zero_drift_pct;
  float span_drift_pct;
  const char* qaqc_status;

  // Vật lý khí thải
  float flue_flow_m3h;
  float flue_temp_c;
  float flue_pressure_gauge_kpa;

  // Môi trường (DHT11)
  float ambient_temp_c;
  float ambient_humidity_pct;

  // RATA metadata
  const char* last_rata_date;
  float rata_accuracy_pct;
};

// ===== Mock sensors =====
float mock_co2() {
  return 15.0 + random(-20, 21) / 10.0;
}
float mock_flow() {
  return 2150.0 + random(-300, 301);
}
float mock_temp() {
  return 150.0 + random(-150, 151) / 10.0;
}
float mock_pressure() {
  // Industrial flue gas gauge pressure in kPa (standard range -50.0 to 50.0 kPa)
  // Default simulated around 0.5 to 2.5 kPa
  return 1.5 + (random(-100, 101) / 100.0); // ~0.50 to 2.51 kPa
}
float mock_so2() {
  return 85.0 + random(-150, 151) / 10.0;  // ppm, dao động vừa
}
float mock_nox() {
  return 120.0 + random(-300, 301) / 10.0; // ppm, dao động rộng
}
float mock_co() {
  return 35.0 + random(-80, 81) / 10.0;    // ppm, dao động hẹp-vừa
}
float mock_o2() {
  return 18.0 + random(-30, 31) / 10.0;    // %, dao động hẹp quanh 15-21%
}
float mock_pm() {
  return 12.0 + random(-50, 51) / 10.0;    // mg/m3, dao động trung bình
}
float mock_zero_drift() {
  return random(-15, 16) / 10.0;           // % drift quanh 0
}
float mock_span_drift() {
  return 3.0;
}

// ===== DHT11 Real Sensor Reading =====
float read_ambient_temp() {
  float t = dht.readTemperature();
  if (isnan(t)) {
    Serial.println("[WARN] Doc nhiet do DHT11 that bai");
    return -999.0;
  }
  return t;
}

float read_ambient_humidity() {
  float h = dht.readHumidity();
  if (isnan(h)) {
    Serial.println("[WARN] Doc do am DHT11 that bai");
    return -999.0;
  }
  return h;
}

// ===== Filter MA(5) & History =====
void fill_history() {
  for (int i = 0; i < WINDOW_SIZE; i++) {
    co2_history[i] = mock_co2();
    so2_history[i] = mock_so2();
    nox_history[i] = mock_nox();
    co_history[i] = mock_co();
    o2_history[i] = mock_o2();
    pm_history[i] = mock_pm();
    flow_history[i] = mock_flow();
    temp_history[i] = 150.0;
    flue_pressure_gauge_history[i] = mock_pressure();
    humidity_history[i] = 60.0;
  }
  sample_index = 0;
}

SensorData get_raw_sample() {
  SensorData raw;
  raw.boot_id = boot_id;
  raw.sample_seq = sample_seq + 1;
  raw.stack_id = STACK_ID;
  raw.measurement_config_id = MEASUREMENT_CONFIG_ID;
  raw.time_quality = (time(nullptr) > 100000) ? "ntp_synced" : "gateway_assigned";
  raw.is_calibrating = 0;

  raw.co2_pct = mock_co2();
  raw.so2_ppm = mock_so2();
  raw.nox_ppm = mock_nox();
  raw.co_ppm = mock_co();
  raw.o2_pct = mock_o2();
  raw.pm_mgm3 = mock_pm();

  raw.flue_flow_m3h = mock_flow();
  raw.flue_temp_c = mock_temp();
  raw.flue_pressure_gauge_kpa = mock_pressure();
  if (raw.flue_pressure_gauge_kpa < -50.0) raw.flue_pressure_gauge_kpa = -50.0;
  if (raw.flue_pressure_gauge_kpa > 50.0) raw.flue_pressure_gauge_kpa = 50.0;

  float dht_t = read_ambient_temp();
  float dht_h = read_ambient_humidity();
  raw.ambient_temp_c = (!isnan(dht_t) && dht_t > -900.0) ? dht_t : 28.0;
  raw.ambient_humidity_pct = (!isnan(dht_h) && dht_h > -900.0) ? dht_h : 60.0;

  raw.zero_drift_pct = mock_zero_drift();
  raw.span_drift_pct = mock_span_drift();
  raw.qaqc_status = (raw.zero_drift_pct > -2.0 && raw.zero_drift_pct < 2.0 && raw.span_drift_pct > -2.0 && raw.span_drift_pct < 2.0) ? "in_control" : "out_of_control";

  raw.last_rata_date = "2026-08-20";
  raw.rata_accuracy_pct = 1.5;

  return raw;
}

SensorData get_filtered_data() {
  float raw_co2 = mock_co2();
  float raw_so2 = mock_so2();
  float raw_nox = mock_nox();
  float raw_co = mock_co();
  float raw_o2 = mock_o2();
  float raw_pm = mock_pm();
  float raw_flow = mock_flow();
  float raw_flue_temp = mock_temp();
  float raw_amb_temp = read_ambient_temp();
  float raw_amb_humidity = read_ambient_humidity();
  float raw_press = mock_pressure();

  if (raw_press < -50.0) raw_press = -50.0;
  if (raw_press > 50.0) raw_press = 50.0;

  co2_history[sample_index] = raw_co2;
  so2_history[sample_index] = raw_so2;
  nox_history[sample_index] = raw_nox;
  co_history[sample_index] = raw_co;
  o2_history[sample_index] = raw_o2;
  pm_history[sample_index] = raw_pm;
  flow_history[sample_index] = raw_flow;
  temp_history[sample_index] = raw_flue_temp;
  flue_pressure_gauge_history[sample_index] = raw_press;
  
  if (!isnan(raw_amb_humidity) && raw_amb_humidity > -900.0) {
    humidity_history[sample_index] = raw_amb_humidity;
  }
  sample_index = (sample_index + 1) % WINDOW_SIZE;

  SensorData avg;
  avg.co2_pct = 0; avg.so2_ppm = 0; avg.nox_ppm = 0;
  avg.co_ppm = 0; avg.o2_pct = 0; avg.pm_mgm3 = 0;
  avg.flue_flow_m3h = 0; avg.flue_temp_c = 0; avg.flue_pressure_gauge_kpa = 0;
  avg.ambient_temp_c = (!isnan(raw_amb_temp) && raw_amb_temp > -900.0) ? raw_amb_temp : 28.0;
  avg.ambient_humidity_pct = 0;
  
  for (int i = 0; i < WINDOW_SIZE; i++) {
    avg.co2_pct += co2_history[i];
    avg.so2_ppm += so2_history[i];
    avg.nox_ppm += nox_history[i];
    avg.co_ppm += co_history[i];
    avg.o2_pct += o2_history[i];
    avg.pm_mgm3 += pm_history[i];
    avg.flue_flow_m3h += flow_history[i];
    avg.flue_temp_c += temp_history[i];
    avg.flue_pressure_gauge_kpa += flue_pressure_gauge_history[i];
    avg.ambient_humidity_pct += humidity_history[i];
  }
  avg.co2_pct /= WINDOW_SIZE;
  avg.so2_ppm /= WINDOW_SIZE;
  avg.nox_ppm /= WINDOW_SIZE;
  avg.co_ppm /= WINDOW_SIZE;
  avg.o2_pct /= WINDOW_SIZE;
  avg.pm_mgm3 /= WINDOW_SIZE;
  avg.flue_flow_m3h /= WINDOW_SIZE;
  avg.flue_temp_c /= WINDOW_SIZE;
  avg.flue_pressure_gauge_kpa /= WINDOW_SIZE;
  avg.ambient_humidity_pct /= WINDOW_SIZE;

  avg.zero_drift_pct = mock_zero_drift();
  avg.span_drift_pct = mock_span_drift();
  avg.qaqc_status = (avg.zero_drift_pct > -2.0 && avg.zero_drift_pct < 2.0 && avg.span_drift_pct > -2.0 && avg.span_drift_pct < 2.0) ? "in_control" : "out_of_control";

  avg.last_rata_date = "2026-08-20";
  avg.rata_accuracy_pct = 1.5;

  return avg;
}

// ===== Buzzer Alert Functions =====
void alert_qaqc_out_of_control() {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(500);
  digitalWrite(BUZZER_PIN, LOW);
  delay(200);
  digitalWrite(BUZZER_PIN, HIGH);
  delay(500);
  digitalWrite(BUZZER_PIN, LOW);
}

void alert_mqtt_disconnected() {
  Serial.println("[ALERT] MQTT disconnected/freeze! Buzzer canh bao dinh ky...");
  digitalWrite(BUZZER_PIN, HIGH);
  delay(100);
  digitalWrite(BUZZER_PIN, LOW);
}

void alert_buffer_full() {
  Serial.println("[ALERT] Buffer TRAN BO DEM (20/20 mau, ~200s)! Buzzer bao dong don dap...");
  for (int i = 0; i < 3; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(80);
    digitalWrite(BUZZER_PIN, LOW);
    delay(80);
  }
}

// ===== Buffer Store-and-Forward =====
bool store_payload(const char* payload) {
  if (buffer_count >= BUFFER_SIZE) {
    // Tràn bộ đệm: bật LED đỏ cảnh báo
    digitalWrite(LED_BUFFER_FULL_PIN, HIGH);
    // Kích hoạt còi báo động dồn dập
    alert_buffer_full();
    // Drop mẫu mới nhất có log tường minh (thừa nhận trung thực giới hạn lưu đệm 200s)
    Serial.println("[WARN] Buffer FULL (20/20 samples, ~200s limit reached). Dropping newest sample!");
    return false;
  }
  strncpy(payload_buffer[buffer_tail], payload, MAX_PAYLOAD_LEN - 1);
  payload_buffer[buffer_tail][MAX_PAYLOAD_LEN - 1] = '\0';
  buffer_tail = (buffer_tail + 1) % BUFFER_SIZE;
  buffer_count++;
  Serial.print("[BUFFER] Stored sample, count=");
  Serial.print(buffer_count);
  Serial.print("/");
  Serial.println(BUFFER_SIZE);

  if (buffer_count >= BUFFER_SIZE) {
    digitalWrite(LED_BUFFER_FULL_PIN, HIGH);
    alert_buffer_full();
    Serial.println("[WARN] Buffer is now FULL (20/20 samples, ~200s capacity reached). LED_BUFFER_FULL ON.");
  }
  return true;
}

bool send_from_buffer() {
  if (buffer_count == 0) return false;
  char* payload = payload_buffer[buffer_head];
  if (mqtt.publish(MQTT_TOPIC, payload)) {
    Serial.print("[TX] (from buffer) ");
    Serial.println(payload);
    buffer_head = (buffer_head + 1) % BUFFER_SIZE;
    buffer_count--;
    Serial.print("[BUFFER] Remaining: ");
    Serial.println(buffer_count);
    return true;
  }
  return false;
}

void flush_buffer(unsigned long delay_ms) {
  while (buffer_count > 0) {
    if (!send_from_buffer()) {
      Serial.println("[BUFFER] Flush interrupted by MQTT publish failure.");
      break;
    }
    if (delay_ms > 0) {
      delay(delay_ms);
    }
  }
  if (buffer_count == 0) {
    Serial.println("[BUFFER] Buffer fully flushed. Turning off Red LED and stopping alert.");
    digitalWrite(LED_BUFFER_FULL_PIN, LOW);
  }
}

// ===== Timestamp =====
String iso_timestamp(String& time_quality) {
  time_t now = time(nullptr);
  if (now > 100000) {
    struct tm tmv;
    gmtime_r(&now, &tmv);
    char buf[40];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S.000Z", &tmv);
    time_quality = "ntp_synced";
    return String(buf);
  }
  time_quality = "gateway_assigned";
  return "";
}

// ===== LCD Display =====
void update_lcd(const SensorData& data) {
  if (!lcd_found) return;
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("CO2:");
  lcd.print(data.co2_pct, 1);
  lcd.print("% T:");
  lcd.print(data.flue_temp_c, 0);
  lcd.print("C");

  lcd.setCursor(0, 1);
  if (strcmp(data.qaqc_status, "out_of_control") == 0) {
    lcd.print("QA/QC: WARNING!");
  } else {
    lcd.print("QA/QC:OK H:");
    lcd.print(data.ambient_humidity_pct, 0);
    lcd.print("%");
  }
}

// ===== Publish =====
void publish_new_data() {
  // 1. Mẫu thô tức thời 10s dùng cho Data Contract lên Edge Gateway
  SensorData raw = get_raw_sample();

  // 2. Mẫu trung bình trượt MA-5 chỉ dùng cho hiển thị màn hình LCD tại hiện trường
  SensorData filtered = get_filtered_data();
  update_lcd(filtered);

  if (strcmp(raw.qaqc_status, "out_of_control") == 0) {
    Serial.println("[ALERT] QA/QC out_of_control - Buzzer kich hoat!");
    alert_qaqc_out_of_control();
  }

  sample_seq++;
  String time_quality;
  String ts_str = iso_timestamp(time_quality);

  char payload[MAX_PAYLOAD_LEN];
  snprintf(payload, sizeof(payload),
    "{"
    "\"device_id\":\"%s\","
    "\"boot_id\":\"%s\","
    "\"sample_seq\":%u,"
    "\"stack_id\":\"%s\","
    "\"measurement_config_id\":\"%s\","
    "\"sample_ts\":\"%s\","
    "\"time_quality\":\"%s\","
    "\"is_calibrating\":%d,"
    "\"location\":{\"lat\":%.4f,\"lon\":%.4f},"
    "\"co2_pct\":%.2f,"
    "\"so2_ppm\":%.2f,"
    "\"nox_ppm\":%.2f,"
    "\"co_ppm\":%.2f,"
    "\"o2_pct\":%.2f,"
    "\"pm_mgm3\":%.2f,"
    "\"flue_flow_m3h\":%.2f,"
    "\"flue_temp_c\":%.2f,"
    "\"flue_pressure_gauge_kpa\":%.2f,"
    "\"ambient_temp_c\":%.2f,"
    "\"ambient_humidity_pct\":%.2f,"
    "\"zero_drift_pct\":%.2f,"
    "\"span_drift_pct\":%.2f,"
    "\"qaqc_status\":\"%s\","
    "\"metric_quality\":{\"co2_pct\":\"good\",\"flue_flow_m3h\":\"good\",\"flue_temp_c\":\"good\",\"flue_pressure_gauge_kpa\":\"good\"},"
    "\"last_rata_date\":\"%s\","
    "\"rata_accuracy_pct\":%.2f"
    "}",
    DEVICE_ID,
    boot_id,
    sample_seq,
    STACK_ID,
    MEASUREMENT_CONFIG_ID,
    ts_str.c_str(),
    time_quality.c_str(),
    raw.is_calibrating,
    LAT, LON,
    raw.co2_pct,
    raw.so2_ppm,
    raw.nox_ppm,
    raw.co_ppm,
    raw.o2_pct,
    raw.pm_mgm3,
    raw.flue_flow_m3h,
    raw.flue_temp_c,
    raw.flue_pressure_gauge_kpa,
    raw.ambient_temp_c,
    raw.ambient_humidity_pct,
    raw.zero_drift_pct,
    raw.span_drift_pct,
    raw.qaqc_status,
    raw.last_rata_date,
    raw.rata_accuracy_pct
  );

  bool is_connected = !simulated_outage && mqtt.connected();

  if (!is_connected) {
    // Khi MQTT disconnected / freeze:
    // Cảnh báo Buzzer định kỳ nếu bộ đệm chưa đầy
    if (buffer_count < BUFFER_SIZE) {
      alert_mqtt_disconnected();
    }
    // Lưu vào bộ đệm RAM (tự động bật LED_BUFFER_FULL_PIN & còi dồn dập nếu buffer đầy)
    store_payload(payload);
  } else {
    // Khi MQTT đang kết nối bình thường:
    store_payload(payload);
    flush_buffer(20);
  }
}

// ===== WiFi + MQTT =====
void blink_led(int count, int duration_ms) {
  for (int i = 0; i < count; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(duration_ms);
    digitalWrite(LED_PIN, LOW);
    delay(duration_ms);
  }
}

bool connect_wifi() {
  Serial.print("[WiFi] Connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED) {
    blink_led(1, 250);
    Serial.print(".");
    if (millis() - started >= WIFI_TIMEOUT_MS) {
      Serial.println(" TIMEOUT");
      return false;
    }
  }
  Serial.println(" OK");
  Serial.print("[WiFi] IP: ");
  Serial.println(WiFi.localIP());
  return true;
}

bool connect_mqtt() {
  if (mqtt.connected()) return true;
  String client_id = String(DEVICE_ID) + "-" + String(random(1000, 9999));
  Serial.print("[MQTT] Connecting to local broker (");
  Serial.print(MQTT_HOST);
  Serial.print(")...");

  bool ok = false;
  if (strlen(MQTT_USER) > 0) {
    ok = mqtt.connect(client_id.c_str(), MQTT_USER, MQTT_PASS);
  } else {
    ok = mqtt.connect(client_id.c_str());
  }

  if (ok) {
    Serial.println(" OK");
    return true;
  }
  Serial.print(" FAIL rc=");
  Serial.println(mqtt.state());
  return false;
}

// ===== SETUP =====
void setup() {
  Serial.begin(115200);
  Serial.println("\n[BOOT] CEMS Node v2.1 - Sensors + LCD + DHT11");

  pinMode(LED_PIN, OUTPUT);
  pinMode(LED_BUFFER_FULL_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
  digitalWrite(LED_BUFFER_FULL_PIN, LOW);

  // Test bíp 1 tiếng ngắn khi khởi động để xác nhận còi hoạt động
  digitalWrite(BUZZER_PIN, HIGH);
  delay(150);
  digitalWrite(BUZZER_PIN, LOW);

  pinMode(0, INPUT_PULLUP);
  randomSeed(micros());
  
  // Khởi tạo boot_id duy nhất cho phiên làm việc
  uint32_t r1 = random(0x10000000, 0x7FFFFFFF);
  uint32_t r2 = random(0x10000000, 0x7FFFFFFF);
  snprintf(boot_id, sizeof(boot_id), "%08x-%04x-%04x-%04x-%08x%04x",
           (unsigned int)r1,
           (unsigned int)(r2 & 0xFFFF),
           (unsigned int)((r1 >> 16) & 0xFFFF),
           (unsigned int)((r2 >> 16) & 0xFFFF),
           (unsigned int)r1,
           (unsigned int)(r2 & 0xFFFF));
  Serial.print("[BOOT] Session boot_id: ");
  Serial.println(boot_id);

  // Khởi động cảm biến DHT11
  dht.begin();
  
  // Khởi động I2C chuẩn cho ESP32 WROOM (SDA=GPIO21, SCL=GPIO22)
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(100); // Tránh kẹt bus I2C nếu thiết bị không phản hồi

  // Quét kiểm tra sự hiện diện của LCD 1602 (0x27 hoặc 0x3F)
  Wire.beginTransmission(0x27);
  if (Wire.endTransmission() == 0) {
    lcd_found = true;
    Serial.println("[LCD] Phat hien man hinh LCD 1602 tai dia chi 0x27.");
  } else {
    Wire.beginTransmission(0x3F);
    if (Wire.endTransmission() == 0) {
      lcd_found = true;
      Serial.println("[LCD] Phat hien man hinh LCD tai dia chi 0x3F.");
    }
  }

  if (lcd_found) {
    lcd.init();
    lcd.backlight();
    lcd.setCursor(0, 0);
    lcd.print("CEMS Node Ready");
    lcd.setCursor(0, 1);
    lcd.print("Init system...");
  } else {
    Serial.println("[LCD] [WARN] Khong tim thay LCD 1602 tren bus I2C (0x27/0x3F). Khoi dong che do khong man hinh (headless).");
  }
  
  fill_history();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);

  wifi_ok = connect_wifi();
  if (!wifi_ok) {
    Serial.println("[BOOT] WiFi failed, halting");
    if (lcd_found) {
      lcd.setCursor(0, 1);
      lcd.print("WiFi: FAILED   ");
    }
    return;
  }

  configTime(7 * 3600, 0, "pool.ntp.org");
  mqtt_ok = connect_mqtt();
  if (!mqtt_ok) {
    Serial.println("[MQTT] Will retry in loop");
  }

  Serial.println("\n================================================");
  Serial.println(" CEMS NODE v2.1 READY");
  Serial.println(" 9 sensors + DHT11 + LCD 16x2 + QA/QC Alert");
  Serial.println(" Local Mosquitto Broker + Buffer Overflow Red LED");
  Serial.println(" Flue Pressure Gauge kPa (-50 to +50 kPa)");
  Serial.println("================================================\n");
}

// ===== LOOP =====
void loop() {
  if (!wifi_ok) {
    delay(1000);
    return;
  }

  if (!simulated_outage) {
    mqtt.loop();
  }

  static bool last_boot_state = HIGH;
  bool current_boot_state = digitalRead(0);
  if (last_boot_state == HIGH && current_boot_state == LOW) {
    simulated_outage = !simulated_outage;
    if (simulated_outage) {
      Serial.println("\n[SIMULATION] >>> OUTAGE ENABLED (MQTT DISCONNECTED) <<<");
      mqtt.disconnect();
    } else {
      Serial.println("\n[SIMULATION] >>> OUTAGE DISABLED (RECOVERING) <<<");
    }
    delay(200);
  }
  last_boot_state = current_boot_state;

  unsigned long now = millis();
  if (now - last_publish_ms >= PUBLISH_INTERVAL_MS) {
    last_publish_ms = now;

    if (simulated_outage) {
      publish_new_data();
    } else {
      if (mqtt.connected()) {
        publish_new_data();
      } else {
        if (connect_mqtt()) {
          Serial.println("[MQTT] Reconnected! Flushing buffer cleanly...");
          flush_buffer(50);
          publish_new_data();
        } else {
          publish_new_data();
        }
      }
    }
  }
}
