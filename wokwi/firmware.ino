/*
 * Decision Final — Firmware ESP32 para Wokwi
 * ============================================
 * Lee potenciómetro (GPIO34), DHT22 (GPIO4) y LDR/fotoresistencia (GPIO35)
 * cada 1 segundo y publica un JSON al broker MQTT público de HiveMQ.
 *
 * Dependencias de Arduino:
 *   - WiFi.h          (incluida con el core de ESP32)
 *   - PubSubClient    (Nick O'Leary) — instalar desde Library Manager
 *   - DHT sensor library (Adafruit) — instalar desde Library Manager
 *
 * Configuración MQTT:
 *   Broker : broker.hivemq.com
 *   Puerto : 1883
 *   Topic  : decisionfinal/sensores
 *   Payload: {"ts":<millis>,"pot":<0-4095>,"temp":<float>,"hum":<float>,"luz":<0-4095>}
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---------------------------------------------------------------------------
// Pines
// ---------------------------------------------------------------------------
#define PIN_POT   34   // Potenciómetro  (ADC1_CH6)
#define PIN_DHT   4    // DHT22
#define PIN_LDR   35   // LDR fotoresistencia (ADC1_CH7)
#define PIN_LED   2    // LED integrado / blink en cada publicación

// ---------------------------------------------------------------------------
// WiFi — credenciales de Wokwi Guest
// ---------------------------------------------------------------------------
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
const char* MQTT_HOST    = "broker.hivemq.com";
const int   MQTT_PORT    = 1883;
const char* MQTT_TOPIC   = "decisionfinal/sensores";
// ID único para evitar colisiones si hay múltiples instancias
String mqttClientId;

// ---------------------------------------------------------------------------
// Sensor DHT22
// ---------------------------------------------------------------------------
DHT dht(PIN_DHT, DHT22);

// ---------------------------------------------------------------------------
// Objetos globales
// ---------------------------------------------------------------------------
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ---------------------------------------------------------------------------
// Intervalo de publicación
// ---------------------------------------------------------------------------
const unsigned long PUBLISH_INTERVAL_MS = 1000;
unsigned long lastPublish = 0;

// ---------------------------------------------------------------------------
// Prototipo de funciones
// ---------------------------------------------------------------------------
void connectWiFi();
void connectMQTT();
void publishSensors();

// ===========================================================================
// setup()
// ===========================================================================
void setup() {
  Serial.begin(115200);
  delay(200);

  // Configurar LED
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  // Configurar ADC (atenuación para leer hasta 3.3 V)
  analogSetAttenuation(ADC_11db);

  // Inicializar DHT22
  dht.begin();

  // ID del cliente MQTT basado en MAC
  mqttClientId = "esp32-df-" + String((uint32_t)ESP.getEfuseMac(), HEX);

  // Conectar WiFi
  connectWiFi();

  // Configurar servidor MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setKeepAlive(60);

  // Conectar MQTT
  connectMQTT();

  Serial.println("[SETUP] Listo.");
}

// ===========================================================================
// loop()
// ===========================================================================
void loop() {
  // Mantener conexión WiFi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Desconectado — reconectando…");
    connectWiFi();
  }

  // Mantener conexión MQTT
  if (!mqtt.connected()) {
    Serial.println("[MQTT] Desconectado — reconectando…");
    connectMQTT();
  }
  mqtt.loop();

  // Publicar cada PUBLISH_INTERVAL_MS
  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = now;
    publishSensors();
  }
}

// ===========================================================================
// connectWiFi() — bloquea hasta estar conectado
// ===========================================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("[WIFI] Conectando a '%s'…", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - t0 > 15000) {
      Serial.println("\n[WIFI] Tiempo agotado — reintentando…");
      WiFi.disconnect();
      delay(1000);
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      t0 = millis();
    }
  }
  Serial.printf("\n[WIFI] Conectado — IP: %s\n", WiFi.localIP().toString().c_str());
}

// ===========================================================================
// connectMQTT() — intenta conexión con backoff lineal
// ===========================================================================
void connectMQTT() {
  int retries = 0;
  while (!mqtt.connected() && retries < 5) {
    Serial.printf("[MQTT] Intentando conectar a %s:%d (id=%s)…\n",
                  MQTT_HOST, MQTT_PORT, mqttClientId.c_str());
    if (mqtt.connect(mqttClientId.c_str())) {
      Serial.println("[MQTT] Conectado.");
    } else {
      retries++;
      Serial.printf("[MQTT] Fallo rc=%d — reintento %d/5\n", mqtt.state(), retries);
      delay(2000 * retries);  // backoff lineal
    }
  }
}

// ===========================================================================
// publishSensors() — lee sensores y publica JSON
// ===========================================================================
void publishSensors() {
  // Leer potenciómetro
  int pot = analogRead(PIN_POT);   // 0–4095

  // Leer DHT22
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  // Si el DHT devuelve NaN (aún calentando), usar valores seguros
  if (isnan(temp)) temp = 0.0f;
  if (isnan(hum))  hum  = 0.0f;

  // Leer LDR
  int luz = analogRead(PIN_LDR);   // 0–4095

  // Construir payload JSON
  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"ts\":%lu,\"pot\":%d,\"temp\":%.2f,\"hum\":%.2f,\"luz\":%d}",
           millis(), pot, temp, hum, luz);

  // Publicar
  bool ok = mqtt.publish(MQTT_TOPIC, payload);

  // Blink LED
  digitalWrite(PIN_LED, HIGH);
  delay(50);
  digitalWrite(PIN_LED, LOW);

  Serial.printf("[PUB] %s %s\n", payload, ok ? "OK" : "FAIL");
}
