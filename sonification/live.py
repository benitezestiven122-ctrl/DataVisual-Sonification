"""Módulo de conexión MQTT en tiempo real para Streamlit y scripts locales.

Al ser un módulo importado, su estado persiste en sys.modules durante los
reruns de Streamlit (a diferencia del script principal que se reejecuta desde cero).
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "decisionfinal/sensores"
BUFFER_MAX = 300

_LOCK = threading.Lock()
_BUFFER: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX)
_LAST: dict[str, Any] = {}
_ERROR: str = ""
_CLIENT: Any = None
_CONNECTED: bool = False
_SUBSCRIBED: bool = False
_MSG_COUNT: int = 0


def is_connected() -> bool:
    with _LOCK:
        return _CONNECTED


def is_subscribed() -> bool:
    with _LOCK:
        return _SUBSCRIBED


def get_error() -> str:
    with _LOCK:
        return _ERROR


def get_count() -> int:
    with _LOCK:
        return _MSG_COUNT


def get_last() -> dict[str, Any]:
    with _LOCK:
        return dict(_LAST)


def get_buffer() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_BUFFER)


def connect(broker: str = MQTT_BROKER, port: int = MQTT_PORT, topic: str = MQTT_TOPIC) -> None:
    """Inicia el cliente MQTT en un hilo daemon si no está conectado."""
    global _CLIENT, _CONNECTED, _SUBSCRIBED, _ERROR, _MSG_COUNT

    with _LOCK:
        if _CONNECTED and _CLIENT is not None:
            return  # Ya está corriendo
        _BUFFER.clear()
        _LAST.clear()
        _ERROR = ""
        _MSG_COUNT = 0
        _CONNECTED = True
        _SUBSCRIBED = False

    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError:
        with _LOCK:
            _ERROR = "paho-mqtt no está instalado en este entorno. Revisa requirements.txt."
            _CONNECTED = False
        return

    def on_connect(client, userdata, flags, rc, properties=None):
        global _ERROR, _SUBSCRIBED
        if rc == 0:
            client.subscribe(topic)
            with _LOCK:
                _ERROR = ""
                _SUBSCRIBED = True
            print(f"[MQTT] Conectado y suscrito a {topic}")
        else:
            with _LOCK:
                _ERROR = f"Error al conectar con broker MQTT (código rc={rc})"
                _SUBSCRIBED = False
            print(f"[MQTT] Error al conectar rc={rc}")

    def on_message(client, userdata, msg):
        global _MSG_COUNT
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            frame = {
                "ts": payload.get("ts", time.time() * 1000),
                "pot": float(payload.get("pot", 0)),
                "temp": float(payload.get("temp", 0)),
                "hum": float(payload.get("hum", 0)),
                "luz": float(payload.get("luz", 0)),
            }
            with _LOCK:
                _BUFFER.append(frame)
                _LAST.clear()
                _LAST.update(frame)
                _MSG_COUNT += 1
            print(f"[MQTT] Lectura #{_MSG_COUNT}: {frame}")
        except Exception as exc:
            print(f"[MQTT] Error decodificando mensaje: {exc}")

    client_id = f"df-web-{int(time.time() * 1000) % 1000000}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id=client_id)

    client.on_connect = on_connect
    client.on_message = on_message

    def _loop_thread():
        global _ERROR, _CONNECTED, _SUBSCRIBED
        try:
            client.connect(broker, port, keepalive=60)
            client.loop_forever()
        except Exception as exc:
            with _LOCK:
                _ERROR = f"Error de conexión MQTT: {exc}"
                _CONNECTED = False
                _SUBSCRIBED = False
            print(f"[MQTT] Error en hilo: {exc}")

    t = threading.Thread(target=_loop_thread, daemon=True)
    t.start()
    _CLIENT = client


def disconnect() -> None:
    """Detiene y desconecta el cliente MQTT."""
    global _CLIENT, _CONNECTED, _SUBSCRIBED
    with _LOCK:
        client = _CLIENT
        _CLIENT = None
        _CONNECTED = False
        _SUBSCRIBED = False

    if client is not None:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass
    print("[MQTT] Desconectado.")


def publish_test() -> bool:
    """Envía un mensaje de prueba al topic MQTT para verificar si el broker responde."""
    with _LOCK:
        client = _CLIENT
    if client is None:
        return False
    test_payload = json.dumps({
        "ts": int(time.time() * 1000),
        "pot": 2048,
        "temp": 23.5,
        "hum": 52.0,
        "luz": 850,
    })
    try:
        res = client.publish(MQTT_TOPIC, test_payload)
        return res.rc == 0
    except Exception:
        return False


def inject_test_frame(pot: float = 2048, temp: float = 23.5, hum: float = 52.0, luz: float = 850) -> None:
    """Inserta directamente una lectura en el buffer (para probar sin depender del broker)."""
    global _MSG_COUNT
    frame = {
        "ts": time.time() * 1000,
        "pot": pot,
        "temp": temp,
        "hum": hum,
        "luz": luz,
    }
    with _LOCK:
        _BUFFER.append(frame)
        _LAST.clear()
        _LAST.update(frame)
        _MSG_COUNT += 1

