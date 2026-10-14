"""Generare procedurală de piese pentru datasetul de inspecție vizuală.

Modulul produce roata dințată folosită ca piesă de referință a celulei CNC, împreună cu
eticheta ei. Geometria și eticheta ies din aceeași decizie, luată înainte de randare, deci
nu se pot dezsincroniza.

Vezi :mod:`cncdt.cnc_cell.parts.gear` pentru parametri și catalogul de defecte și
:mod:`cncdt.cnc_cell.parts.manifest` pentru formatul de etichete.
"""

from cncdt.cnc_cell.parts.gear import (
    DEFECT_CLASSES,
    NOMINAL,
    PartVariant,
    GearParams,
    build_mesh,
    build_variant,
    build_variant_from_params,
)
from cncdt.cnc_cell.parts.manifest import LabelRecord, read_manifest, write_manifest

__all__ = [
    "DEFECT_CLASSES",
    "NOMINAL",
    "GearParams",
    "LabelRecord",
    "PartVariant",
    "build_mesh",
    "build_variant",
    "build_variant_from_params",
    "read_manifest",
    "write_manifest",
]
