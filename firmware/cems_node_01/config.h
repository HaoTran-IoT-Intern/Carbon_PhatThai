#ifndef CEMS_CONFIG_H
#define CEMS_CONFIG_H

// WiFi Configuration
#define DEFAULT_WIFI_SSID "pioneglobal.com"
#define DEFAULT_WIFI_PASS "pioneglobal.com_3F"
#define DEFAULT_WIFI_TIMEOUT_MS 15000

// MQTT Configuration (Local Edge Gateway Mosquitto Broker)
#define DEFAULT_MQTT_HOST "192.168.80.37"
#define DEFAULT_MQTT_PORT 1883
#define DEFAULT_MQTT_USER "cems_node"
#define DEFAULT_MQTT_PASS "cems_secret_pass"
#define DEFAULT_MQTT_TOPIC "pione/carbon/v1/cems_node_01/data"

// Node & Stack Identity
#define DEFAULT_DEVICE_ID "cems_node_01"
#define DEFAULT_STACK_ID "stack_01"
#define DEFAULT_MEASUREMENT_CONFIG_ID "cfg_cement_stack_v1"

// Geolocation
#define DEFAULT_NODE_LAT 21.0278
#define DEFAULT_NODE_LON 105.8342

#endif // CEMS_CONFIG_H
