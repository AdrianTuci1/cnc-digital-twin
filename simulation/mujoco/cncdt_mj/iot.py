"""Publicarea telemetriei geamănului MuJoCo către AWS IoT Core.

Certificatul mTLS al dispozitivului și endpoint-ul vin din ``simulation/secrets/``, scrise de
``simulation/scripts/gen-iot-certs.sh`` din ieșirile Terraform. Modulul nu citește `.env` și nu
ține secrete în repo; dacă fișierele lipsesc, publicarea se oprește cu un mesaj care spune ce
comandă trebuie rulată.

Eșantioanele publicate sunt exact rândurile din ``samples.jsonl``, adică înregistrările din
contractul ``cnc.telemetry.v1``. Firehose le grupează pe ferestre, iar Lambda-ul le transformă în
caracteristici; de aceea aici nu se adaugă câmpuri.
"""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from typing import Any, Mapping

#: Directorul cu certificate, relativ la repository.
SECRETS_DIR = Path(__file__).resolve().parents[2] / "secrets"

#: Numele fișierelor scrise de scriptul de certificate.
CA_FILE = "AmazonRootCA1.pem"
CERT_FILE = "device.pem.crt"
KEY_FILE = "private.pem.key"
ENV_FILE = "iot-endpoint.env"


def read_env_file(path: Path) -> dict[str, str]:
    """Perechile ``CHEIE=valoare`` dintr-un fișier, fără comentarii și fără ghilimele."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class IotPublisher:
    """Conexiunea MQTT către IoT Core, cu certificat de dispozitiv.

    ``topic`` este cel din ieșirea Terraform (``cncdt/<mediu>/cnc/<celulă>/telemetry``), iar
    ``client_id`` trebuie să fie unic pe cont: IoT Core închide conexiunea veche când apare
    aceeași identitate, deci două simulări simultane ar sta pe rând.
    """

    def __init__(
        self,
        *,
        secrets_dir: Path | None = None,
        topic: str | None = None,
        client_id: str | None = None,
        qos: int = 1,
    ) -> None:
        directory = Path(secrets_dir) if secrets_dir else SECRETS_DIR
        env = {**read_env_file(directory / ENV_FILE), **os.environ}
        host = env.get("CNCDT_MQTT_HOST", "")
        if not host:
            raise RuntimeError(
                f"lipsește {directory / ENV_FILE}; rulează simulation/scripts/gen-iot-certs.sh"
                " după ce stack-ul de platformă este aplicat")
        self.topic = topic or env.get("CNCDT_TELEMETRY_TOPIC", "")
        if not self.topic:
            raise RuntimeError("lipsește CNCDT_TELEMETRY_TOPIC din iot-endpoint.env")
        self.port = int(env.get("CNCDT_MQTT_PORT", "8883"))
        self.host = host
        self.sent = 0
        self.failed = 0

        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or env.get("CNCDT_MQTT_CLIENT_ID", "cncdt-mujoco"),
            protocol=mqtt.MQTTv311)
        self._client.tls_set(
            ca_certs=str(directory / CA_FILE),
            certfile=str(directory / CERT_FILE),
            keyfile=str(directory / KEY_FILE),
            tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.connect(host, self.port, keepalive=30)
        self._client.loop_start()

    def publish(self, record: Mapping[str, Any]) -> None:
        """Trimite un eșantion; o eroare de rețea nu oprește simularea."""
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        info = self._client.publish(self.topic, payload, qos=1)
        if info.rc == self._mqtt.MQTT_ERR_SUCCESS:
            self.sent += 1
        else:
            self.failed += 1

    def close(self) -> None:
        """Așteaptă coada, apoi închide curat."""
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # pragma: no cover - închiderea nu are voie să cadă la final
            pass
