"""Captura cadrelor de inspecție și trimiterea lor către pipeline.

Camera de inspecție este sursa celui de-al doilea model. Fiecare piesă finită primește un
cadru, salvat ca PNG, plus un fișier JSON cu metadatele piesei. Fișierul JSON este cel care
declanșează Lambda-ul de inspecție: regula S3 filtrează pe prefixul ``raw/qa-snapshots/``
și pe sufixul ``.json``.

Cadrul are un rol, scris în câmpul ``camera_role``:

- ``sorting`` — camera de la zona lamei/piston; verdictul ei decide ruta piesei;
- ``inspection`` — camera de la ieșirea din mașină, pentru comparație și pentru dataset.

Captura folosește Replicator, disponibil în Isaac Sim. Dacă modulul lipsește, se încearcă
captura directă a viewport-ului. Dacă ambele lipsesc, se scrie doar metadatele, iar pipeline-ul
rămâne testabil.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def log(message: str, level: str = "info") -> None:
    try:  # pragma: no cover - disponibil doar în Isaac Sim
        import carb  # type: ignore

        getattr(carb, "log_warn" if level == "warn" else "log_info")(f"[cnc_cell] {message}")
    except Exception:
        print(f"[cnc_cell][{level}] {message}", flush=True)


def _try_replicator_capture(output_path: Path, camera_path: str | None = None) -> bool:
    """Captură prin Replicator. Întoarce True dacă a scris fișierul.

    ``camera_path`` este camera de inspecție legată din scenă; fără ea se folosește
    camera de perspectivă implicită a Kit-ului.
    """
    try:  # pragma: no cover - necesită Isaac Sim
        import omni.replicator.core as rep  # type: ignore

        render_product = rep.create.render_product(
            camera_path or "/OmniverseKit_Persp", (1280, 720)
        )
        writer = rep.WriterRegistry.get("BasicWriter")
        writer.initialize(output_dir=str(output_path.parent), rgb=True)
        writer.attach([render_product])
        rep.orchestrator.step()
        writer.detach()
        render_product.destroy()
        return any(output_path.parent.glob("rgb_*.png"))
    except Exception as exc:
        log(f"captura Replicator a eșuat: {exc}", level="warn")
        return False


def _try_viewport_capture(output_path: Path) -> bool:
    """Captură directă a viewport-ului, fără Replicator."""
    try:  # pragma: no cover - necesită Isaac Sim
        import omni.kit.viewport.utility as viewport_utils  # type: ignore

        viewport = viewport_utils.get_active_viewport()
        if viewport is None:
            return False
        viewport_utils.capture_viewport_to_file(viewport, str(output_path))
        return output_path.exists()
    except Exception as exc:
        log(f"captura viewport-ului a eșuat: {exc}", level="warn")
        return False


@dataclass
class QaSnapshotWriter:
    """Scrie cadrele de inspecție și metadatele lor.

    Când este configurat un bucket S3, fișierele se urcă acolo. Altfel rămân local, sub
    ``output_dir``, iar pipeline-ul poate fi testat prin copiere manuală.
    """

    output_dir: Path
    bucket: str = ""
    prefix: str = "raw/qa-snapshots"
    # Gol lasă boto3 să ia regiunea din mediu (AWS_REGION), ca să nu hardcodăm una greșită.
    aws_region: str = ""

    _s3_client: Any = field(default=None, init=False)
    written: int = field(default=0, init=False)
    uploads: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        # În container calea este /logs/qa-snapshots. În afara lui (dezvoltare locală,
        # teste) directorul poate lipsi sau nu poate fi creat; cădem pe un director temporar
        # în loc să oprim controlerul.
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fallback = Path(tempfile.gettempdir()) / "cncdt-qa-snapshots"
            log(f"{self.output_dir} nu poate fi creat ({exc}); folosesc {fallback}", level="warn")
            self.output_dir = fallback
            self.output_dir.mkdir(parents=True, exist_ok=True)

    def _client(self) -> Any:
        if self._s3_client is None:
            try:  # pragma: no cover - depinde de mediul de rulare
                import boto3  # type: ignore

                self._s3_client = (
                    boto3.client("s3", region_name=self.aws_region)
                    if self.aws_region
                    else boto3.client("s3")
                )
            except Exception as exc:
                log(f"boto3 indisponibil, imaginile rămân locale: {exc}", level="warn")
                self._s3_client = False
        return self._s3_client or None

    def capture(
        self,
        cycle_id: str,
        part_id: str,
        metadata: dict[str, Any],
        capture_image: bool = True,
        camera_path: str | None = None,
        camera_role: str = "inspection",
    ) -> dict[str, Any]:
        """Salvează cadrul și metadatele unei piese. Întoarce descrierea scrisă.

        Metadatele sunt scrise chiar dacă imaginea nu poate fi capturată, pentru ca
        pipeline-ul de control să poată fi testat separat de redarea grafică.
        ``camera_path`` este camera din scenă, când a fost legată, iar ``camera_role``
        spune ce post de inspecție a produs cadrul (``sorting`` ori ``inspection``).
        """
        directory = self.output_dir / cycle_id
        directory.mkdir(parents=True, exist_ok=True)

        image_path = directory / f"{part_id}.png"
        captured = False
        if capture_image:
            captured = _try_replicator_capture(image_path, camera_path) or _try_viewport_capture(
                image_path
            )
            if not captured:
                log("nu am putut captura cadrul; se scriu doar metadatele", level="warn")

        record: dict[str, Any] = {
            "schema_version": "1.1.0",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cycle_id": cycle_id,
            "part_id": part_id,
            "camera_role": camera_role,
            "image_captured": captured,
            "image_key": f"{self.prefix}/{cycle_id}/{part_id}.png" if captured else "",
            "camera": camera_path or "active_viewport",
            "resolution": [1280, 720],
        }
        record.update(metadata)

        sidecar = directory / f"{part_id}.json"
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.written += 1

        self._upload(sidecar, f"{self.prefix}/{cycle_id}/{part_id}.json")
        if captured:
            self._upload(image_path, f"{self.prefix}/{cycle_id}/{part_id}.png")

        return record

    def _upload(self, path: Path, key: str) -> None:
        client = self._client()
        if not self.bucket or client is None:
            return
        try:
            extra = {"ContentType": "application/json"} if path.suffix == ".json" else {"ContentType": "image/png"}
            client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)
            self.uploads += 1
        except Exception as exc:  # pragma: no cover - depinde de rețea
            log(f"urcarea {key} a eșuat: {exc}", level="warn")

    def status_line(self) -> str:
        target = f"s3://{self.bucket}/{self.prefix}" if self.bucket else str(self.output_dir)
        return f"{target} | capturi: {self.written} | urcări: {self.uploads}"
