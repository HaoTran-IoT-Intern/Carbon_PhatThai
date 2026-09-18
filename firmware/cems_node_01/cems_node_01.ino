/*
 * ESP32 CEMS Node - Hướng 1 (Bên phát thải) - v2.1
 * 6 khí+bụi, 3 vật lý (DHT11 temp + humidity, flow, flue_pressure_gauge_kpa), QA/QC mock
 * LCD 16x2 I2C, Buzzer alert, Buffer store-and-forward (20 samples ~200s limit)
 * Local Mosquitto Broker with authentication & Buffer full Red LED indicator
 */

#include <WiFi.h>
#include <DHT.h>
#define MQTT_MAX_PACKET_SIZE 512
#include <PubSubClient.h>
#include <time.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define DHTPIN 4
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

#define BUZZER_PIN 18
#define LED_BUFFER_FULL_PIN 23

// I2C pins for ESP32-S3 (Default: SDA=GPIO8, SCL=GPIO9 hoặc SDA=GPIO21, SCL=GPIO20)
#define I2C_SDA 8
#define I2C_SCL 9

LiquidCrystal_I2C lcd(0x27, 16, 2);

#define WINDOW_SIZE 5
#define BUFFER_SIZE 20
#define MAX_PAYLOAD_LEN 512

// WiFi
const char* WIFI_SSID = "pioneglobal.com";
const char* WIFI_PASS = "pioneglobal.com_3F";
const unsigned long WIFI_TIMEOUT_MS = 15000;

// MQTT Broker Configuration (Local Edge Gateway Mosquitto)
const char* MQTT_HOST = "192.168.1.100";  // Local Edge Gateway IP
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER = "cems_node";       // Local Mosquitto Auth User
const char* MQTT_PASS = "cems_secret_pass"; // Local Mosquitto Auth Password
const char* MQTT_TOPIC = "pione/carbon/v1/cems_node_01/data";

// Node info
const char* DEVICE_ID = "cems_node_01";
const float LAT = 21.0278;
const float LON = 105.8342;

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
  // Vật lý
  float flow_m3h;
  float temp_c;
  float flue_pressure_gauge_kpa;
  float humidity_pct;
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
    temp_history[i] = 28.0;       // Nhiệt độ phòng khởi tạo hợp lý
    flue_pressure_gauge_history[i] = mock_pressure();
    humidity_history[i] = 60.0;   // Độ ẩm phòng khởi tạo hợp lý
  }
  sample_index = 0;
}

SensorData get_filtered_data() {
  float raw_co2 = mock_co2();
  float raw_so2 = mock_so2();
  float raw_nox = mock_nox();
  float raw_co = mock_co();
  float raw_o2 = mock_o2();
  float raw_pm = mock_pm();
  float raw_flow = mock_flow();
  float raw_temp = read_ambient_temp();
  float raw_humidity = read_ambient_humidity();
  float raw_press = mock_pressure();

  // Clamp raw pressure to valid physical gauge range [-50.0, 50.0] kPa
  if (raw_press < -50.0) raw_press = -50.0;
  if (raw_press > 50.0) raw_press = 50.0;

  co2_history[sample_index] = raw_co2;
  so2_history[sample_index] = raw_so2;
  nox_history[sample_index] = raw_nox;
  co_history[sample_index] = raw_co;
  o2_history[sample_index] = raw_o2;
  pm_history[sample_index] = raw_pm;
  flow_history[sample_index] = raw_flow;
  
  // Chỉ cập nhật nếu đọc DHT11 thành công, tránh làm hỏng dữ liệu trung bình
  if (!isnan(raw_temp) && raw_temp > -900.0) {
    temp_history[sample_index] = raw_temp;
  }
  if (!isnan(raw_humidity) && raw_humidity > -900.0) {
    humidity_history[sample_index] = raw_humidity;
  }

  flue_pressure_gauge_history[sample_index] = raw_press;
  sample_index = (sample_index + 1) % WINDOW_SIZE;

  SensorData avg;
  avg.co2_pct = 0; avg.so2_ppm = 0; avg.nox_ppm = 0;
  avg.co_ppm = 0; avg.o2_pct = 0;
  avg.pm_mgm3 = 0;
  avg.flow_m3h = 0; avg.temp_c = 0; avg.flue_pressure_gauge_kpa = 0; avg.humidity_pct = 0;
  
  for (int i = 0; i < WINDOW_SIZE; i++) {
    avg.co2_pct += co2_history[i];
    avg.so2_ppm += so2_history[i];
    avg.nox_ppm += nox_history[i];
    avg.co_ppm += co_history[i];
    avg.o2_pct += o2_history[i];
    avg.pm_mgm3 += pm_history[i];
    avg.flow_m3h += flow_history[i];
    avg.temp_c += temp_history[i];
    avg.flue_pressure_gauge_kpa += flue_pressure_gauge_history[i];
    avg.humidity_pct += humidity_history[i];
  }
  avg.co2_pct /= WINDOW_SIZE;
  avg.so2_ppm /= WINDOW_SIZE;
  avg.nox_ppm /= WINDOW_SIZE;
  avg.co_ppm /= WINDOW_SIZE;
  avg.o2_pct /= WINDOW_SIZE;
  avg.pm_mgm3 /= WINDOW_SIZE;
  avg.flow_m3h /= WINDOW_SIZE;
  avg.temp_c /= WINDOW_SIZE;
  avg.flue_pressure_gauge_kpa /= WINDOW_SIZE;
  avg.humidity_pct /= WINDOW_SIZE;

  // QA/QC
  avg.zero_drift_pct = mock_zero_drift();
  avg.span_drift_pct = mock_span_drift();
  avg.qaqc_status = (avg.zero_drift_pct > -2.0 && avg.zero_drift_pct < 2.0 && avg.span_drift_pct > -2.0 && avg.span_drift_pct < 2.0) ? "in_control" : "out_of_control";

  // RATA metadata
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

// ===== Timestamp =====
String iso_timestamp() {
  time_t now = time(nullptr);
  if (now > 100000) {
    struct tm tmv;
    localtime_r(&now, &tmv);
    char buf[40];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S%z", &tmv);
    return String(buf);
  }
  return "uptime_s:" + String(millis() / 1000);
}

// ===== LCD Display =====
void update_lcd(const SensorData& data) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("CO2:");
  lcd.print(data.co2_pct, 1);
  lcd.print("% T:");
  lcd.print(data.temp_c, 0);
  lcd.print("C");

  lcd.setCursor(0, 1);
  if (strcmp(data.qaqc_status, "out_of_control") == 0) {
    lcd.print("QA/QC: WARNING!");
  } else {
    lcd.print("QA/QC:OK H:");
    lcd.print(data.humidity_pct, 0);
    lcd.print("%");
  }
}

// ===== Publish =====
void publish_new_data() {
  SensorData data = get_filtered_data();

  if (strcmp(data.qaqc_status, "out_of_control") == 0) {
    Serial.println("[ALERT] QA/QC out_of_control - Buzzer kich hoat!");
    alert_qaqc_out_of_control();
  }

  // Cập nhật thông số lên màn hình LCD
  update_lcd(data);

  char payload[MAX_PAYLOAD_LEN];
  snprintf(payload, sizeof(payload),
    "{"
    "\"device_id\":\"%s\","
    "\"ts\":\"%s\","
    "\"location\":{\"lat\":%.4f,\"lon\":%.4f},"
    "\"co2_pct\":%.2f,"
    "\"so2_ppm\":%.2f,"
    "\"nox_ppm\":%.2f,"
    "\"co_ppm\":%.2f,"
    "\"o2_pct\":%.2f,"
    "\"pm_mgm3\":%.2f,"
    "\"flow_m3h\":%.2f,"
    "\"temp_c\":%.2f,"
    "\"flue_pressure_gauge_kpa\":%.2f,"
    "\"humidity_pct\":%.2f,"
    "\"zero_drift_pct\":%.2f,"
    "\"span_drift_pct\":%.2f,"
    "\"qaqc_status\":\"%s\","
    "\"last_rata_date\":\"%s\","
    "\"rata_accuracy_pct\":%.2f"
    "}",
    DEVICE_ID,
    iso_timestamp().c_str(),
    LAT, LON,
    data.co2_pct,
    data.so2_ppm,
    data.nox_ppm,
    data.co_ppm,
    data.o2_pct,
    data.pm_mgm3,
    data.flow_m3h,
    data.temp_c,
    data.flue_pressure_gauge_kpa,
    data.humidity_pct,
    data.zero_drift_pct,
    data.span_drift_pct,
    data.qaqc_status,
    data.last_rata_date,
    data.rata_accuracy_pct
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
    while (buffer_count > 0) {
      if (!send_from_buffer()) break;
      delay(20);
    }
    if (buffer_count == 0) {
      digitalWrite(LED_BUFFER_FULL_PIN, LOW);
    }
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
  
  // Khởi động cảm biến DHT11
  dht.begin();
  
  // Khởi động I2C và màn hình LCD (SDA=GPIO8, SCL=GPIO9)
  Wire.begin(I2C_SDA, I2C_SCL);
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("CEMS Node Ready");
  lcd.setCursor(0, 1);
  lcd.print("Init system...");
  
  fill_history();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);

  wifi_ok = connect_wifi();
  if (!wifi_ok) {
    Serial.println("[BOOT] WiFi failed, halting");
    lcd.setCursor(0, 1);
    lcd.print("WiFi: FAILED   ");
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
          while (buffer_count > 0) {
            if (!send_from_buffer()) {
              Serial.println("[BUFFER] Flush interrupted by MQTT publish failure.");
              break;
            }
            delay(50);
          }
          if (buffer_count == 0) {
            Serial.println("[BUFFER] Buffer fully flushed. Turning off Red LED and stopping alert.");
            digitalWrite(LED_BUFFER_FULL_PIN, LOW);
          }
          publish_new_data();
        } else {
          publish_new_data();
        }
      }
    }
  }
}
