"""Configurația extensiei, citită din variabile de mediu.

Valorile au valori implicite sigure, ca extensia să poată fi încărcată și fără niciun
mediu pregătit: publicarea se face atunci într-un fișier local, nu către AWS IoT Core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class CellConfig:
    """Parametrii celulei CNC și ai legăturii de date."""

    # Identitate
    cell_id: str = field(default_factory=lambda: os.environ.get("CNCDT_CELL_ID", "cell-a"))
    device_id: str = field(default_factory=lambda: os.environ.get("CNCDT_DEVICE_ID", "cnc-cell-01"))

    # Fereastra de telemetrie: publicatorul trimite un pachet agregat la acest interval.
    window_seconds: int = field(default_factory=lambda: _env_int("CNCDT_WINDOW_SECONDS", 1))
    # Fereastra trimisă către Firehose (micro-batch). Firehose grupează oricum la 60 s.
    flush_seconds: int = field(default_factory=lambda: _env_int("CNCDT_FLUSH_SECONDS", 60))

    # Transport
    mqtt_host: str = field(default_factory=lambda: os.environ.get("CNCDT_MQTT_HOST", ""))
    mqtt_port: int = field(default_factory=lambda: _env_int("CNCDT_MQTT_PORT", 8883))
    telemetry_topic: str = field(
        default_factory=lambda: os.environ.get(
            "CNCDT_TELEMETRY_TOPIC", "cncdt/dev/cnc/cell-a/telemetry"
        )
    )
    control_topic_prefix: str = field(
        default_factory=lambda: os.environ.get("CNCDT_CONTROL_TOPIC_PREFIX", "cncdt/dev/cnc/cell-a")
    )
    cert_dir: str = field(default_factory=lambda: os.environ.get("CNCDT_CERT_DIR", ""))
    ca_file: str = field(default_factory=lambda: os.environ.get("CNCDT_CA_FILE", ""))
    cert_file: str = field(default_factory=lambda: os.environ.get("CNCDT_CERT_FILE", ""))
    key_file: str = field(default_factory=lambda: os.environ.get("CNCDT_KEY_FILE", ""))
    # Fără broker configurat, telemetria se scrie local, ca fișier JSON Lines.
    local_log_path: str = field(
        default_factory=lambda: os.environ.get("CNCDT_TELEMETRY_LOG", "/logs/telemetry.jsonl")
    )

    # Stocare de imagini. Gol înseamnă scriere locală în directorul de loguri.
    s3_bucket: str = field(default_factory=lambda: os.environ.get("CNCDT_QA_BUCKET", ""))
    s3_prefix: str = field(default_factory=lambda: os.environ.get("CNCDT_QA_PREFIX", "raw/qa-snapshots"))
    # Regiunea clientului S3. Containerul primește AWS_REGION de la start-isaac.sh.
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "eu-central-1"))
    qa_output_dir: str = field(
        default_factory=lambda: os.environ.get("CNCDT_QA_OUTPUT_DIR", "/logs/qa-snapshots")
    )

    # Model de defectare
    tool_wear_rate: float = field(
        default_factory=lambda: _env_float("CNCDT_TOOL_WEAR_RATE", 0.0015)
    )
    joint_friction_rate: float = field(
        default_factory=lambda: _env_float("CNCDT_JOINT_FRICTION_RATE", 0.0008)
    )
    random_defect_probability: float = field(
        default_factory=lambda: _env_float("CNCDT_RANDOM_DEFECT_PROB", 0.05)
    )

    # Comportament
    autostart_cycle: bool = field(default_factory=lambda: _env_bool("CNCDT_AUTOSTART_CYCLE", False))
    # Sortare. Implicit, ruta unei piese este decisă de verdictul din cloud. Cu opțiunea
    # pornită, previziunea locală a degradării sortează ea piesa atunci când cloudul nu a
    # răspuns, ca demonstrația să funcționeze și fără pipeline.
    sort_on_local_verdict: bool = field(
        default_factory=lambda: _env_bool("CNCDT_SORT_ON_LOCAL", False)
    )
    # Poarta de sortare de la lamă. Pornită, fiecare piesă este oprită la capătul benzii de
    # inspecție până când sosește un verdict: cel din cloud (autoritar) ori previziunea
    # locală, la sfârșitul ciclului. Oprită, comportamentul rămâne cel de dinainte:
    # verdictul mișcă opritorul fără să oprească piesa.
    diverter_enabled: bool = field(
        default_factory=lambda: _env_bool("CNCDT_DIVERTER_ENABLED", True)
    )
    # Ruta de rezervă când nu sosește niciun verdict. Implicit rebut: o piesă neverificată
    # nu se livrează. Se scrie „pass" pentru a prefera continuitatea producției.
    diverter_fail_safe: str = field(
        default_factory=lambda: os.environ.get("CNCDT_DIVERTER_FAIL_SAFE", "scrap").strip().lower()
    )
    verbose: bool = field(default_factory=lambda: _env_bool("CNCDT_VERBOSE", False))

    @property
    def diverter_fail_safe_route(self) -> str:
        """Ruta de rezervă, normalizată la ``GOOD`` ori ``SCRAP``."""
        return "GOOD" if self.diverter_fail_safe in {"pass", "good", "ok"} else "SCRAP"

    @property
    def has_mqtt(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def has_mtls(self) -> bool:
        return bool(self.cert_dir and self.ca_file and self.cert_file and self.key_file)

    def resolved_tls_paths(self) -> dict[str, str]:
        """Căile efective ale certificatelor, compuse din ``cert_dir`` când sunt relative."""
        if not self.cert_dir:
            return {}
        base = self.cert_dir.rstrip("/")
        return {
            "ca_certs": self.ca_file or f"{base}/AmazonRootCA1.pem",
            "certfile": self.cert_file or f"{base}/device.pem.crt",
            "keyfile": self.key_file or f"{base}/private.pem.key",
        }

    def telemetry_topic_for(self, suffix: str = "telemetry") -> str:
        """Compune un topic sub prefixul de control, de exemplu verdictul de inspecție."""
        return f"{self.control_topic_prefix}/{suffix}"
