"""Publicarea telemetriei către AWS IoT Core.

Trei moduri, alese automat:

1. **MQTT cu mTLS** către AWS IoT Core (sau către Mosquitto local, cu certificatele
   dispozitivului). Necesită ``paho-mqtt`` și certificatele din ``simulation/secrets``.
2. **MQTT simplu** către un broker local, fără TLS. Folosit în dezvoltare.
3. **Fișier local** JSON Lines. Rezervă, ca simularea să nu se oprească niciodată din
   cauza rețelei.

Trimiterea se face dintr-un fir de execuție separat, ca să nu blocheze bucla de simulare.
Înregistrările se acumulează într-un buffer și se trimit periodic.
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import CellConfig

try:  # paho-mqtt este opțional: fără el, publicarea se face în fișier local.
    import paho.mqtt.client as mqtt

    HAS_PAHO = True
except ImportError:  # pragma: no cover - depinde de mediul de rulare
    mqtt = None  # type: ignore[assignment]
    HAS_PAHO = False


def log(message: str, level: str = "info") -> None:
    """Jurnalizare către Kit când este disponibil, altfel către stdout."""
    try:  # pragma: no cover - disponibil doar în Isaac Sim
        import carb  # type: ignore

        getattr(carb.log_info if level == "info" else carb.log_warn)(f"[cnc_cell] {message}")
    except Exception:
        print(f"[cnc_cell][{level}] {message}", flush=True)


@dataclass
class PublishStats:
    """Contoare pentru panoul de control."""

    published: int = 0
    dropped: int = 0
    failed: int = 0
    last_error: str = ""
    connected: bool = False
    transport: str = "none"
    last_publish_ts: float = 0.0


@dataclass
class TelemetryPublisher:
    """Buffer cu trimitere periodică pe MQTT sau în fișier local.

    ``control_callback`` este apelat pentru fiecare mesaj primit pe topicul de comandă,
    cu dicționarul decodat. Aici ajunge verdictul de inspecție din cloud, care acționează
    pistonul de rejecție.
    """

    config: CellConfig
    control_callback: Callable[[dict[str, Any]], None] | None = None
    stats: PublishStats = field(default_factory=PublishStats)

    _buffer: list[dict[str, Any]] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _queue: queue.Queue = field(default_factory=queue.Queue, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _client: Any = field(default=None, init=False)
    _file_handle: Any = field(default=None, init=False)
    _buffer_bytes: int = field(default=0, init=False)

    # ------------------------------------------------------------ ciclu de viață
    def start(self) -> None:
        """Pornește transportul și firul de execuție."""
        if self._thread is not None:
            return

        self._stop_event.clear()

        if self.config.has_mqtt and HAS_PAHO:
            self._start_mqtt()
        else:
            if self.config.has_mqtt and not HAS_PAHO:
                log("paho-mqtt lipsește; telemetria merge în fișier local", level="warn")
            self._start_file()

        self._thread = threading.Thread(target=self._run, name="cncdt-telemetry", daemon=True)
        self._thread.start()
        log(f"publicare pornită prin {self.stats.transport}")

    def stop(self, flush: bool = True) -> None:
        """Oprește transportul, trimițând ultimele înregistrări."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if flush:
            self.flush()
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # pragma: no cover - curățenie defensivă
                pass
            self._client = None
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None
        self.stats.connected = False

    # ------------------------------------------------------------ transport
    def _start_mqtt(self) -> None:
        tls_paths = self.config.resolved_tls_paths()
        use_tls = self.config.mqtt_port != 1883 and bool(tls_paths)

        try:
            client = mqtt.Client(
                client_id=self.config.device_id,
                protocol=mqtt.MQTTv311,
                clean_session=True,
            )
        except TypeError:  # paho 2.x folosește un apel diferit
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=self.config.device_id)

        if use_tls:
            client.tls_set(
                ca_certs=tls_paths["ca_certs"],
                certfile=tls_paths["certfile"],
                keyfile=tls_paths["keyfile"],
            )
            client.tls_insecure_set(False)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self._client = client
        self.stats.transport = "mqtt-mtls" if use_tls else "mqtt"
        try:
            client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
            client.loop_start()
        except Exception as exc:  # pragma: no cover - depinde de rețea
            self.stats.last_error = str(exc)
            log(f"conectarea MQTT a eșuat ({exc}); trec pe fișier local", level="warn")
            self._client = None
            self._start_file()

    def _start_file(self) -> None:
        path = Path(self.config.local_log_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = path.open("a", encoding="utf-8")
        except OSError as exc:
            # /logs există în container, dar nu pe o mașină de dezvoltare. Cădem pe un
            # director temporar ca simularea să nu se oprească.
            fallback = Path(tempfile.gettempdir()) / "cncdt-telemetry.jsonl"
            log(f"{path} nu poate fi deschis ({exc}); scriu în {fallback}", level="warn")
            path = fallback
            self._file_handle = path.open("a", encoding="utf-8")
        self.stats.transport = "file"

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc != 0:
            self.stats.last_error = f"cod de conectare {rc}"
            log(f"MQTT a refuzat conexiunea: cod {rc}", level="warn")
            return
        self.stats.connected = True
        log("conectat la brokerul MQTT")
        for suffix in ("command", "qa-verdict", "chaos"):
            topic = self.config.telemetry_topic_for(suffix)
            client.subscribe(topic, qos=1)
            log(f"abonat la {topic}")

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        self.stats.connected = False
        if not self._stop_event.is_set():
            log(f"deconectat de la MQTT (cod {rc}); reconectare automată", level="warn")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log(f"mesaj de control invalid pe {message.topic}: {exc}", level="warn")
            return

        log(f"comandă primită pe {message.topic}: {payload}")
        if self.control_callback is not None:
            try:
                self.control_callback(payload)
            except Exception as exc:  # pragma: no cover - callback-ul e al aplicației
                log(f"callback-ul de control a eșuat: {exc}", level="warn")

    # ------------------------------------------------------------ trimitere
    def publish(self, record: dict[str, Any]) -> None:
        """Adaugă o înregistrare în buffer. Nu blochează."""
        try:
            self._queue.put_nowait(record)
        except queue.Full:  # pragma: no cover - coada nu are limită
            self.stats.dropped += 1

    def flush(self) -> None:
        """Trimite tot ce este în buffer."""
        with self._lock:
            pending, self._buffer = self._buffer, []
            self._buffer_bytes = 0
        if not pending:
            return
        self._send(pending)

    def _drain_queue(self) -> None:
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._buffer.append(record)

    def _run(self) -> None:
        """Firul de execuție: drenează coada și trimite la intervalul configurat."""
        interval = max(1, self.config.flush_seconds)
        next_flush = time.monotonic() + interval
        while not self._stop_event.is_set():
            self._drain_queue()
            if time.monotonic() >= next_flush:
                self.flush()
                next_flush = time.monotonic() + interval
            time.sleep(0.2)
        self._drain_queue()

    def _send(self, records: list[dict[str, Any]]) -> None:
        if self._client is not None:
            self._send_mqtt(records)
        elif self._file_handle is not None:
            self._send_file(records)

    def _send_mqtt(self, records: list[dict[str, Any]]) -> None:
        """Trimite înregistrările, grupat în loturi de cel mult 100."""
        batch: list[dict[str, Any]] = []
        for record in records:
            batch.append(record)
            if len(batch) >= 100:
                self._publish_batch(batch)
                batch = []
        if batch:
            self._publish_batch(batch)

    def _publish_batch(self, batch: list[dict[str, Any]]) -> None:
        payload = json.dumps(batch, separators=(",", ":"), default=str)
        try:
            info = self._client.publish(self.config.telemetry_topic, payload, qos=1)
            if info.rc == 0:
                self.stats.published += len(batch)
                self.stats.last_publish_ts = time.time()
            else:
                self.stats.failed += len(batch)
                self.stats.last_error = f"rc={info.rc}"
        except Exception as exc:  # pragma: no cover - depinde de rețea
            self.stats.failed += len(batch)
            self.stats.last_error = str(exc)

    def _send_file(self, records: list[dict[str, Any]]) -> None:
        if self._file_handle is None:
            return
        try:
            for record in records:
                self._file_handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
            self._file_handle.flush()
            self.stats.published += len(records)
            self.stats.last_publish_ts = time.time()
        except OSError as exc:  # pragma: no cover - depinde de disc
            self.stats.failed += len(records)
            self.stats.last_error = str(exc)

    # ------------------------------------------------------------ stare
    def status_line(self) -> str:
        error = f" | ultima eroare: {self.stats.last_error}" if self.stats.last_error else ""
        return (
            f"{self.stats.transport} | trimis: {self.stats.published} | "
            f"eșuat: {self.stats.failed}{error}"
        )
