/**
 * SonificationViz.pde — Decision Final: Visualización generativa en tiempo real
 * =============================================================================
 * Processing 4, Java mode.
 *
 * Suscribe a broker.hivemq.com:1883 / topic "decisionfinal/sensores"
 * y renderiza un panel dividido en tres zonas:
 *
 *  ┌─────────────────────────────────────────────────────────────────────────┐
 *  │  HUD (80 px): valores actuales de cada sensor                          │
 *  ├─────────────────────────────────────────────────────────────────────────┤
 *  │                                                                         │
 *  │  ESCENA PRINCIPAL (60 % - HUD): visual generativo según nivel de pot   │
 *  │                                                                         │
 *  ├─────────────────────────────────────────────────────────────────────────┤
 *  │  HISTORIAL (25 %): waveform + heat strip + análisis                    │
 *  └─────────────────────────────────────────────────────────────────────────┘
 *
 * Dependencias:
 *   - MQTT for Processing (Jan Vantomme): Sketch → Import Library → Manage Libraries
 *
 * Autor: Decision Final (generado automáticamente)
 */

import mqtt.*;  // Librería MQTT de Jan Vantomme

// ===========================================================================
// Constantes globales
// ===========================================================================
static final int   W              = 1200;
static final int   H              = 700;
static final int   HUD_H          = 80;    // px — franja HUD superior
static final float HIST_FRAC      = 0.25;  // fracción de altura para historial
static final int   BUFFER_MAX     = 300;   // lecturas máximas en buffer rodante
static final float T_LOW          = 0.30;  // umbral bajo / medio (normalizado 0-1)
static final float T_HIGH         = 0.70;  // umbral medio / alto
static final int   MA_WINDOW      = 20;    // ventana de media móvil
static final int   CORR_WINDOW    = 100;   // ventana para correlación
static final float ANOM_SIGMA     = 2.0;   // desviaciones para detectar anomalía
static final int   ANOM_FLASH_MS  = 500;   // duración del flash de anomalía (ms)
static final float SMOOTH_ALPHA   = 0.15;  // suavizado exponencial de estado
static final float POT_MAX        = 4095;  // rango ADC

// ===========================================================================
// MQTT
// ===========================================================================
static final String MQTT_BROKER = "broker.hivemq.com";
static final int    MQTT_PORT   = 1883;
static final String MQTT_TOPIC  = "decisionfinal/sensores";

MQTTClient mqttClient;

// ===========================================================================
// Buffer y estado global
// ===========================================================================
RollingBuffer buf = new RollingBuffer(BUFFER_MAX);

// Valor suavizado del nivel (0-2 → 0.0-2.0, interpolado)
float smoothLevel = 0;
// Estado de partículas / escena
ParticleSystem particles;

// Tiempos en cada rango (segundos)
float[] dwellTime = { 0, 0, 0 };  // bajo, medio, alto
long lastFrameMs  = 0;

// Anomalía
long anomalyStartMs = -ANOM_FLASH_MS * 2;

// Fonts
PFont fontBig;
PFont fontSmall;

// ===========================================================================
// setup()
// ===========================================================================
void setup() {
  size(1200, 700);           // debe coincidir con W, H (constante no admitida en size())
  colorMode(RGB, 255);
  frameRate(30);
  smooth(4);

  fontBig   = createFont("Courier New Bold", 22);
  fontSmall = createFont("Courier New", 13);

  particles = new ParticleSystem(300);

  lastFrameMs = millis();

  // --- Conexión MQTT ---
  mqttClient = new MQTTClient(this);
  String clientId = "pde-decision-" + (int) random(100000);
  mqttClient.connect("mqtt://" + MQTT_BROKER + ":" + MQTT_PORT, clientId);
}

// ===========================================================================
// Callbacks MQTT (llamados por la librería desde el thread de red)
// ===========================================================================
void clientConnected() {
  println("[MQTT] Conectado a " + MQTT_BROKER);
  mqttClient.subscribe(MQTT_TOPIC);
}

void messageReceived(String topic, byte[] payload) {
  String msg = new String(payload);
  try {
    JSONObject j = parseJSONObject(msg);
    if (j == null) return;
    SensorReading r = new SensorReading(
      j.getLong("ts"),
      j.getInt("pot"),
      j.getFloat("temp"),
      j.getFloat("hum"),
      j.getFloat("luz")
    );
    buf.push(r);
  } catch (Exception e) {
    println("[MQTT] Error parseando: " + msg);
  }
}

void connectionLost() {
  println("[MQTT] Conexión perdida — reconectando…");
  String clientId = "pde-decision-" + (int) random(100000);
  mqttClient.connect("mqtt://" + MQTT_BROKER + ":" + MQTT_PORT, clientId);
}

// ===========================================================================
// draw() — 30 fps
// ===========================================================================
void draw() {
  background(10, 12, 16);  // fondo oscuro global

  long nowMs = millis();
  float dt   = (nowMs - lastFrameMs) / 1000.0;
  lastFrameMs = nowMs;

  // Lectura actual y normalización
  SensorReading last   = buf.last();
  float potNorm        = last != null ? norm01(last.pot, 0, POT_MAX) : 0;
  int   lvl            = levelOf(potNorm);

  // Suavizado exponencial del nivel
  smoothLevel += SMOOTH_ALPHA * (lvl - smoothLevel);

  // Dwell time
  dwellTime[lvl] += dt;

  // Detectar anomalía
  float[] stats = buf.rollingStats(MA_WINDOW);  // [mean, std]
  if (stats[1] > 0 && last != null) {
    float zScore = abs(potNorm * 100 - stats[0]) / stats[1];
    if (zScore > ANOM_SIGMA) {
      anomalyStartMs = nowMs;
    }
  }

  // Rate of change
  float roc = buf.rateOfChange();  // 0-1, normalizado

  // --- Zonas de dibujo ---
  int mainTop    = HUD_H;
  int histH      = (int)(H * HIST_FRAC);
  int mainH      = H - HUD_H - histH;
  int histTop    = HUD_H + mainH;

  drawHUD(last, potNorm, lvl, 0, 0, W, HUD_H);
  drawMainScene(potNorm, smoothLevel, lvl, roc, mainTop, mainH, stats, nowMs);
  drawHistory(histTop, histH);
}

// ===========================================================================
// HUD — franja superior con valores actuales
// ===========================================================================
void drawHUD(SensorReading r, float potNorm, int lvl, int x, int y, int w, int h) {
  // Fondo HUD
  fill(15, 18, 25);
  noStroke();
  rect(x, y, w, h);

  if (r == null) {
    fill(120);
    textFont(fontSmall);
    textAlign(CENTER, CENTER);
    text("Esperando datos MQTT…", w / 2, y + h / 2);
    return;
  }

  // Colores por rango
  color[] rangeColors = { color(60, 120, 220), color(60, 200, 90), color(220, 60, 60) };
  color hudColor = rangeColors[lvl];

  // Borde inferior de color según rango
  stroke(hudColor);
  strokeWeight(3);
  line(x, y + h - 2, x + w, y + h - 2);
  noStroke();

  // Cuatro bloques de métricas
  String[] labels  = { "POT",             "TEMP",           "HUM",          "LUZ"           };
  String[] values  = {
    nf(r.pot, 0, 0),
    nf(r.temp, 0, 1) + " °C",
    nf(r.hum,  0, 1) + " %",
    nf(r.luz,  0, 0)
  };
  color[] valColors = {
    hudColor,
    color(100, 220, 255),
    color(80, 180, 240),
    color(255, 220, 80)
  };

  int blockW = w / 4;
  for (int i = 0; i < 4; i++) {
    int bx = x + i * blockW;
    // Separador vertical
    if (i > 0) {
      stroke(40, 45, 60);
      strokeWeight(1);
      line(bx, y + 10, bx, y + h - 10);
      noStroke();
    }
    // Etiqueta
    fill(140);
    textFont(fontSmall);
    textAlign(LEFT, TOP);
    text(labels[i], bx + 16, y + 10);
    // Valor
    fill(valColors[i]);
    textFont(fontBig);
    text(values[i], bx + 16, y + 28);
  }

  // Mini barra de rango en la esquina derecha
  String[] rangeLabels = { "BAJO", "MEDIO", "ALTO" };
  fill(rangeColors[lvl]);
  textFont(fontSmall);
  textAlign(RIGHT, CENTER);
  text("▶ " + rangeLabels[lvl], x + w - 16, y + h / 2);
}

// ===========================================================================
// ESCENA PRINCIPAL
// ===========================================================================
void drawMainScene(float potNorm, float smoothLvl, int lvl, float roc,
                   int top, int h, float[] stats, long nowMs) {
  // Clip a la zona principal
  clip(0, top, W, h);

  // Fondo dinámico
  if (smoothLvl < 1.0) {
    // Bajo: azul oscuro
    background(8, 14, 35);
  } else if (smoothLvl < 1.5) {
    // Transición bajo → medio
    float t = smoothLvl - 1.0;
    color bg = lerpColor(color(8, 14, 35), color(10, 22, 18), t);
    background(red(bg), green(bg), blue(bg));
  } else if (smoothLvl < 2.0) {
    // Medio: verde oscuro
    color bg = lerpColor(color(10, 22, 18), color(28, 10, 10), smoothLvl - 1.5);
    background(red(bg), green(bg), blue(bg));
  } else {
    // Alto: casi rojo
    background(28, 10, 10);
  }

  // Dibujar visual según rango
  if (smoothLvl <= 1.0) {
    // ---- BAJO: campo de partículas Perlin con trails ----
    drawLowScene(potNorm, smoothLvl, top, h, roc);
  } else if (smoothLvl <= 1.8) {
    // ---- MEDIO: polígonos rotantes ----
    drawMidScene(potNorm, smoothLvl - 1.0, top, h, roc);
  } else {
    // ---- ALTO: partículas rápidas + glitch ----
    drawHighScene(potNorm, smoothLvl - 1.5, top, h, roc, nowMs);
  }

  // ---- Overlay de anomalía (borde naranja) ----
  if (nowMs - anomalyStartMs < ANOM_FLASH_MS) {
    float alpha = 255 * (1.0 - (float)(nowMs - anomalyStartMs) / ANOM_FLASH_MS);
    noFill();
    stroke(255, 140, 0, alpha);
    strokeWeight(8);
    rect(4, top + 4, W - 8, h - 8);
    noStroke();
  }

  // ---- Dwell chart (esquina superior derecha) ----
  drawDwellChart(W - 90, top + 10, 70, 90);

  noClip();
}

// ---------------------------------------------------------------------------
// Rango BAJO: campo de Perlin con partículas lentas y trails
// ---------------------------------------------------------------------------
void drawLowScene(float potNorm, float smoothLvl, int top, int h, float roc) {
  particles.setBehavior(0, potNorm, roc);
  particles.update(top, h);
  particles.draw(top, h);
}

// ---------------------------------------------------------------------------
// Rango MEDIO: sistema de polígonos concéntricos rotantes
// ---------------------------------------------------------------------------
void drawMidScene(float potNorm, float blend, int top, int h, float roc) {
  pushMatrix();
  translate(W / 2.0, top + h / 2.0);

  int cx = 0, cy = 0;
  int sides = (int) lerp(3, 7, potNorm);
  int rings = 5;

  for (int r = 0; r < rings; r++) {
    float radius = lerp(30, min(W, h) * 0.44, (float)(r + 1) / rings);
    float angle  = frameCount * (0.004 + roc * 0.012) * (r % 2 == 0 ? 1 : -1);
    // Jitter de humedad (std dev del buffer)
    float jitter = buf.std(50) * 0.5;

    // Color: verde → naranja según temperatura normalizada
    SensorReading last = buf.last();
    float tempNorm = last != null ? norm01(last.temp, 0, 50) : 0.5;
    color ringColor = lerpColor(color(50, 220, 90), color(255, 150, 20), tempNorm);
    int alpha = 180 - r * 20;

    noFill();
    stroke(red(ringColor), green(ringColor), blue(ringColor), alpha);
    strokeWeight(2 - r * 0.3);

    beginShape();
    for (int i = 0; i <= sides; i++) {
      float a = angle + TWO_PI * i / sides;
      float jx = random(-jitter, jitter);
      float jy = random(-jitter, jitter);
      vertex(cos(a) * radius + jx, sin(a) * radius + jy);
    }
    endShape(CLOSE);
  }

  popMatrix();
  noStroke();
}

// ---------------------------------------------------------------------------
// Rango ALTO: partículas rápidas + efecto glitch
// ---------------------------------------------------------------------------
void drawHighScene(float potNorm, float blend, int top, int h, float roc, long nowMs) {
  // Partículas rápidas
  particles.setBehavior(2, potNorm, roc);
  particles.update(top, h);
  particles.draw(top, h);

  // Glitch: copiar tiras horizontales con desplazamiento aleatorio
  if (frameCount % 3 == 0) {
    PImage snap = get(0, top, W, h);
    int strips = (int)(potNorm * 8 + 2);
    for (int s = 0; s < strips; s++) {
      int sy = top + (int) random(h);
      int sh = (int) random(4, 20);
      int dx = (int) random(-40, 40);
      image(snap.get(0, sy - top, W, min(sh, top + h - sy)), dx, sy);
    }
  }

  // Flash de saw-wave en picos
  if (potNorm > 0.9) {
    float alpha = (sin(frameCount * 0.5) * 0.5 + 0.5) * 60;
    fill(255, 40, 40, alpha);
    noStroke();
    rect(0, top, W, h);
  }
}

// ---------------------------------------------------------------------------
// Dwell chart — mini barras verticales (tiempo en cada rango)
// ---------------------------------------------------------------------------
void drawDwellChart(int x, int y, int w, int h) {
  float total = dwellTime[0] + dwellTime[1] + dwellTime[2];
  if (total < 0.01) return;

  color[] rc = { color(60, 120, 220, 180), color(60, 200, 90, 180), color(220, 60, 60, 180) };
  String[] lbl = { "B", "M", "A" };
  int bw = w / 3 - 2;

  fill(0, 0, 0, 100);
  noStroke();
  rect(x - 4, y - 4, w + 8, h + 20);

  for (int i = 0; i < 3; i++) {
    float frac = dwellTime[i] / total;
    int bh = (int)(h * frac);
    fill(rc[i]);
    rect(x + i * (bw + 2), y + h - bh, bw, bh);
    fill(200);
    textFont(fontSmall);
    textAlign(CENTER, TOP);
    text(lbl[i], x + i * (bw + 2) + bw / 2, y + h + 2);
  }
}

// ===========================================================================
// HISTORIAL
// ===========================================================================
void drawHistory(int top, int h) {
  clip(0, top, W, h);

  // Fondo
  fill(10, 12, 16);
  noStroke();
  rect(0, top, W, h);

  int n = buf.size();
  if (n < 2) {
    fill(80);
    textAlign(CENTER, CENTER);
    textFont(fontSmall);
    text("Esperando datos…", W / 2, top + h / 2);
    noClip();
    return;
  }

  // Área para waveform (deja 8 px de heat strip al fondo)
  int heatH    = 8;
  int waveH    = h - heatH;

  // ---- Banda media ± std ----
  float[] maArr    = new float[n];
  float[] stdArr   = new float[n];
  for (int i = 0; i < n; i++) {
    // Media móvil con ventana MA_WINDOW centrada atrás
    int from = max(0, i - MA_WINDOW + 1);
    float sum = 0, sum2 = 0;
    for (int j = from; j <= i; j++) {
      float v = norm01(buf.get(j).pot, 0, POT_MAX) * 100;
      sum  += v;
      sum2 += v * v;
    }
    int cnt   = i - from + 1;
    maArr[i]  = sum / cnt;
    stdArr[i] = sqrt(max(0, sum2 / cnt - (maArr[i] * maArr[i])));
  }

  // Fondo: banda media±std (relleno blanco semi-transparente)
  fill(255, 255, 255, 25);
  noStroke();
  beginShape();
  for (int i = 0; i < n; i++) {
    float x = map(i, 0, n - 1, 0, W);
    float yy = map(maArr[i] + stdArr[i], 0, 100, top + waveH, top);
    vertex(x, yy);
  }
  for (int i = n - 1; i >= 0; i--) {
    float x = map(i, 0, n - 1, 0, W);
    float yy = map(maArr[i] - stdArr[i], 0, 100, top + waveH, top);
    vertex(x, yy);
  }
  endShape(CLOSE);

  // ---- Líneas finas de otros sensores (normalizados 0-100) ----
  drawSensorLine(top, waveH, n, 1, color(100, 160, 255, 80));  // temp
  drawSensorLine(top, waveH, n, 2, color(80, 200, 220, 80));   // hum
  drawSensorLine(top, waveH, n, 3, color(255, 200, 60, 80));   // luz

  // ---- Líneas de umbral (punteadas) ----
  drawDashedLine(0, (int) map(T_LOW  * 100, 0, 100, top + waveH, top), W, color(60, 120, 220, 160));
  drawDashedLine(0, (int) map(T_HIGH * 100, 0, 100, top + waveH, top), W, color(220, 60, 60, 160));

  // ---- Waveform principal (pot), coloreada por rango ----
  strokeWeight(2);
  noFill();
  for (int i = 1; i < n; i++) {
    float v    = norm01(buf.get(i).pot, 0, POT_MAX);
    int   lv   = levelOf(v);
    color[] rc = { color(60, 120, 220), color(60, 200, 90), color(220, 60, 60) };
    stroke(rc[lv]);
    float x0 = map(i - 1, 0, n - 1, 0, W);
    float y0 = map(norm01(buf.get(i - 1).pot, 0, POT_MAX) * 100, 0, 100, top + waveH, top);
    float x1 = map(i,     0, n - 1, 0, W);
    float y1 = map(v * 100, 0, 100, top + waveH, top);
    line(x0, y0, x1, y1);
  }
  noStroke();

  // ---- Media móvil (línea blanca gruesa discontinua) ----
  drawMALine(top, waveH, n, maArr);

  // ---- Playhead (línea blanca vertical al extremo derecho) ----
  stroke(255, 255, 255, 180);
  strokeWeight(1.5);
  line(W - 1, top, W - 1, top + waveH);
  noStroke();

  // ---- Heat strip ----
  for (int i = 0; i < n; i++) {
    float v  = norm01(buf.get(i).pot, 0, POT_MAX);
    int lv   = levelOf(v);
    color[] rc = { color(60, 120, 220), color(60, 200, 90), color(220, 60, 60) };
    fill(rc[lv]);
    float x  = map(i, 0, n - 1, 0, W);
    float xn = map(i + 1, 0, n - 1, 0, W);
    noStroke();
    rect(x, top + waveH, xn - x + 1, heatH);
  }

  // ---- Mini matriz de correlación (esquina inferior derecha) ----
  drawCorrMatrix(W - 90, top + waveH - 90, 80, 80);

  noClip();
}

// ---------------------------------------------------------------------------
// Línea de un sensor secundario normalizado al panel de historial
// ---------------------------------------------------------------------------
void drawSensorLine(int top, int waveH, int n, int sensorIdx, color c) {
  stroke(c);
  strokeWeight(1);
  for (int i = 1; i < n; i++) {
    float v0 = sensorNorm(buf.get(i - 1), sensorIdx);
    float v1 = sensorNorm(buf.get(i),     sensorIdx);
    float x0 = map(i - 1, 0, n - 1, 0, W);
    float y0 = map(v0, 0, 1, top + waveH, top);
    float x1 = map(i,     0, n - 1, 0, W);
    float y1 = map(v1, 0, 1, top + waveH, top);
    line(x0, y0, x1, y1);
  }
  noStroke();
}

// ---------------------------------------------------------------------------
// Línea de media móvil (blanca, punteada gruesa)
// ---------------------------------------------------------------------------
void drawMALine(int top, int waveH, int n, float[] maArr) {
  stroke(255, 255, 255, 200);
  strokeWeight(2.5);
  boolean dash = true;
  for (int i = 1; i < n; i++) {
    if (i % 6 == 0) dash = !dash;
    if (!dash) continue;
    float x0 = map(i - 1, 0, n - 1, 0, W);
    float y0 = map(maArr[i - 1], 0, 100, top + waveH, top);
    float x1 = map(i,     0, n - 1, 0, W);
    float y1 = map(maArr[i], 0, 100, top + waveH, top);
    line(x0, y0, x1, y1);
  }
  noStroke();
}

// ---------------------------------------------------------------------------
// Línea horizontal discontinua (umbral)
// ---------------------------------------------------------------------------
void drawDashedLine(int x0, int y, int x1, color c) {
  stroke(c);
  strokeWeight(1);
  for (int x = x0; x < x1; x += 14) {
    line(x, y, min(x + 7, x1), y);
  }
  noStroke();
}

// ---------------------------------------------------------------------------
// Mini matriz de correlación (pot, temp, hum, luz) — últimas CORR_WINDOW lecturas
// ---------------------------------------------------------------------------
void drawCorrMatrix(int x, int y, int w, int h) {
  int n = min(buf.size(), CORR_WINDOW);
  if (n < 4) return;

  // Recoger datos de cada sensor en arrays
  float[][] vals = new float[4][n];
  for (int i = 0; i < n; i++) {
    SensorReading r = buf.get(buf.size() - n + i);
    vals[0][i] = r.pot;
    vals[1][i] = r.temp;
    vals[2][i] = r.hum;
    vals[3][i] = r.luz;
  }

  int cellW = w / 4;
  int cellH = h / 4;
  String[] names = { "P", "T", "H", "L" };

  // Fondo
  fill(0, 0, 0, 160);
  noStroke();
  rect(x - 2, y - 2, w + 4, h + 20);

  for (int r = 0; r < 4; r++) {
    for (int c = 0; c < 4; c++) {
      float corr = (r == c) ? 1.0 : pearson(vals[r], vals[c], n);
      // Mapeo: -1 → azul, 0 → blanco, +1 → rojo
      color cellColor;
      if (corr >= 0) {
        cellColor = lerpColor(color(240, 240, 255), color(220, 50, 50), corr);
      } else {
        cellColor = lerpColor(color(240, 240, 255), color(50, 80, 220), -corr);
      }
      fill(cellColor, 200);
      noStroke();
      rect(x + c * cellW, y + r * cellH, cellW - 1, cellH - 1);
    }
  }

  // Etiquetas de filas
  textFont(fontSmall);
  fill(200);
  for (int i = 0; i < 4; i++) {
    textAlign(CENTER, CENTER);
    text(names[i], x - 10, y + i * cellH + cellH / 2);
    text(names[i], x + i * cellW + cellW / 2, y + h + 10);
  }
}

// ===========================================================================
// Helpers matemáticos y de mapeo
// ===========================================================================

/** Normaliza val de [min, max] a [0, 1], clampeado. */
float norm01(float val, float mn, float mx) {
  if (mx <= mn) return 0;
  return constrain((val - mn) / (mx - mn), 0, 1);
}

/** Devuelve 0 (bajo), 1 (medio) o 2 (alto) según potNorm (0-1). */
int levelOf(float potNorm) {
  if (potNorm <= T_LOW)  return 0;
  if (potNorm <= T_HIGH) return 1;
  return 2;
}

/** Sensor normalizado 0-1 (0=pot,1=temp,2=hum,3=luz). */
float sensorNorm(SensorReading r, int idx) {
  switch (idx) {
    case 0: return norm01(r.pot,  0, 4095);
    case 1: return norm01(r.temp, 0, 50);
    case 2: return norm01(r.hum,  0, 100);
    case 3: return norm01(r.luz,  0, 4095);
    default: return 0;
  }
}

/** Correlación de Pearson entre dos arrays de longitud n. */
float pearson(float[] a, float[] b, int n) {
  float ma = 0, mb = 0;
  for (int i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  float num = 0, da2 = 0, db2 = 0;
  for (int i = 0; i < n; i++) {
    float da = a[i] - ma, db = b[i] - mb;
    num += da * db; da2 += da * da; db2 += db * db;
  }
  float denom = sqrt(da2 * db2);
  return denom < 1e-9 ? 0 : constrain(num / denom, -1, 1);
}

// ===========================================================================
// Clase SensorReading — una trama de sensores
// ===========================================================================
class SensorReading {
  long  ts;
  float pot, temp, hum, luz;

  SensorReading(long ts, float pot, float temp, float hum, float luz) {
    this.ts   = ts;
    this.pot  = pot;
    this.temp = temp;
    this.hum  = hum;
    this.luz  = luz;
  }
}

// ===========================================================================
// Clase RollingBuffer — buffer circular de SensorReading
// ===========================================================================
class RollingBuffer {
  ArrayList<SensorReading> data;
  int maxSize;

  RollingBuffer(int maxSize) {
    this.maxSize = maxSize;
    data = new ArrayList<SensorReading>(maxSize);
  }

  void push(SensorReading r) {
    data.add(r);
    if (data.size() > maxSize) data.remove(0);
  }

  SensorReading get(int i) { return data.get(i); }
  int size()               { return data.size(); }
  SensorReading last()     { return data.isEmpty() ? null : data.get(data.size() - 1); }

  /** Devuelve [mean_norm, std_norm] de pot sobre la ventana de los últimos w puntos. */
  float[] rollingStats(int w) {
    int n    = min(data.size(), w);
    if (n < 1) return new float[]{ 0, 0 };
    float sum = 0, sum2 = 0;
    int from = data.size() - n;
    for (int i = from; i < data.size(); i++) {
      float v = norm01(data.get(i).pot, 0, POT_MAX) * 100;
      sum  += v;
      sum2 += v * v;
    }
    float mean = sum / n;
    float std  = sqrt(max(0, sum2 / n - mean * mean));
    return new float[]{ mean, std };
  }

  /** Desviación estándar de humedad (últimos w). */
  float std(int w) {
    int n = min(data.size(), w);
    if (n < 2) return 0;
    float sum = 0, sum2 = 0;
    int from = data.size() - n;
    for (int i = from; i < data.size(); i++) {
      float v = data.get(i).hum;
      sum += v; sum2 += v * v;
    }
    float m = sum / n;
    return sqrt(max(0, sum2 / n - m * m));
  }

  /** Tasa de cambio absoluta del pot (diferencia normalizada, 0-1). */
  float rateOfChange() {
    int n = data.size();
    if (n < 2) return 0;
    float sum = 0;
    int window = min(5, n - 1);
    for (int i = n - window; i < n; i++) {
      sum += abs(norm01(data.get(i).pot, 0, POT_MAX) - norm01(data.get(i - 1).pot, 0, POT_MAX));
    }
    return constrain(sum / window * 10, 0, 1);
  }
}

// ===========================================================================
// Clase ParticleSystem — partículas para rangos bajo y alto
// ===========================================================================
class ParticleSystem {
  int count;
  float[] px, py, vx, vy, life, sz;
  int behavior;  // 0=bajo, 2=alto

  ParticleSystem(int count) {
    this.count = count;
    px   = new float[count];
    py   = new float[count];
    vx   = new float[count];
    vy   = new float[count];
    life = new float[count];
    sz   = new float[count];
    for (int i = 0; i < count; i++) spawnParticle(i, 0, 0, W, H);
  }

  void setBehavior(int b, float potNorm, float roc) {
    behavior = b;
  }

  void update(int top, int h) {
    for (int i = 0; i < count; i++) {
      if (behavior == 0) {
        // Bajo: Perlin noise field, movimiento lento
        float angle = noise(px[i] * 0.004, py[i] * 0.004, frameCount * 0.005) * TWO_PI * 2;
        vx[i] = lerp(vx[i], cos(angle) * 0.6, 0.05);
        vy[i] = lerp(vy[i], sin(angle) * 0.6, 0.05);
      } else {
        // Alto: velocidades altas, expansión desde el centro
        float cx = W / 2.0, cy = top + h / 2.0;
        float dx = px[i] - cx, dy = py[i] - cy;
        float d  = max(1, sqrt(dx * dx + dy * dy));
        vx[i] = lerp(vx[i], dx / d * 3.5, 0.08);
        vy[i] = lerp(vy[i], dy / d * 3.5, 0.08);
      }
      px[i] += vx[i];
      py[i] += vy[i];
      life[i] -= 0.008;
      if (life[i] <= 0 || px[i] < 0 || px[i] > W || py[i] < top || py[i] > top + h) {
        spawnParticle(i, top, 0, W, h);
      }
    }
  }

  void draw(int top, int h) {
    noStroke();
    for (int i = 0; i < count; i++) {
      float alpha = life[i] * 180;
      if (behavior == 0) {
        // Azul con profundidad: tamaño proporcional a sz (simula z)
        fill(60 + sz[i] * 40, 100 + sz[i] * 60, 220, alpha);
        ellipse(px[i], py[i], sz[i] * 4, sz[i] * 4);
      } else {
        // Rojo / blanco
        color c = lerpColor(color(220, 60, 60, alpha), color(255, 255, 255, alpha), life[i]);
        fill(c);
        ellipse(px[i], py[i], sz[i] * 3, sz[i] * 3);
      }
    }
  }

  void spawnParticle(int i, int top, int left, int w, int h) {
    if (behavior == 2) {
      // Alto: desde el centro
      px[i] = left + w / 2.0 + random(-20, 20);
      py[i] = top  + h / 2.0 + random(-20, 20);
    } else {
      px[i] = left + random(w);
      py[i] = top  + random(h);
    }
    vx[i]   = random(-0.5, 0.5);
    vy[i]   = random(-0.5, 0.5);
    life[i] = random(0.5, 1.0);
    sz[i]   = random(0.3, 1.0);
  }
}
