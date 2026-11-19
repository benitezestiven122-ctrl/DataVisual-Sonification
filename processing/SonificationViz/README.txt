Decision Final — SonificationViz
=================================
Visualización generativa en tiempo real (Processing 4, Java mode)

INSTRUCCIONES
-------------

1. Instalar Processing 4+
   Descarga: https://processing.org/download

2. Instalar la librería MQTT
   En Processing: Sketch → Import Library → Manage Libraries
   Busca "MQTT" (autor: Jan Vantomme) → Install

3. Abrir y ejecutar el sketch
   File → Open → (esta carpeta) → SonificationViz.pde
   Presiona ▶ (Run)

4. Ejecutar la simulación Wokwi (opcional, para datos reales)
   - Ve a https://wokwi.com
   - Crea un nuevo proyecto ESP32
   - Sube diagram.json  (carpeta: wokwi/)
   - Sube firmware.ino  (carpeta: wokwi/)
   - Presiona ▶ en Wokwi; el ESP32 empezará a publicar MQTT

5. Configuración MQTT (hardcoded, no requiere cambios)
   Broker : broker.hivemq.com
   Puerto : 1883
   Topic  : decisionfinal/sensores
   Payload: {"ts":<millis>,"pot":0-4095,"temp":<float>,"hum":<float>,"luz":0-4095}

ESTRUCTURA DEL VISUAL
---------------------
- HUD (franja superior 80 px)
  Muestra pot / temp / hum / luz en tiempo real, coloreados por rango.

- Escena principal (60 % del panel)
  · Rango BAJO  (pot 0–30 %): campo de partículas Perlin, azul, trails.
  · Rango MEDIO (pot 31–70 %): polígonos rotantes concéntricos,
    color → temperatura, jitter → humedad, velocidad → tasa de cambio.
  · Rango ALTO  (pot 71–100 %): partículas rápidas + glitch strips + flash.
  · Borde naranja si hay anomalía (> 2σ de la media móvil).
  · Mini gráfico de dwell time (tiempo en cada rango) en la esquina superior.

- Historial (25 % inferior)
  · Waveform coloreado por rango.
  · Línea de media móvil (blanca discontinua, ventana = 20).
  · Banda media ± desviación estándar (blanco translúcido).
  · Líneas de umbral bajo / alto (punteadas).
  · Curvas finas de temperatura, humedad y luz normalizadas.
  · Heat strip de 8 px (color de rango de cada muestra).
  · Playhead blanco en la lectura más reciente (extremo derecho).
  · Mini matriz de correlación 4×4 (pot/temp/hum/luz), esquina inferior derecha.

NOTAS
-----
- Si el sketch no conecta al broker, revisa tu conexión a Internet.
- Los datos demo (sin Wokwi) no llegan; el historial mostrará "Esperando datos…"
  hasta que algún cliente publique en el topic.
- Para cambiar broker/topic edita las constantes al inicio de SonificationViz.pde.
