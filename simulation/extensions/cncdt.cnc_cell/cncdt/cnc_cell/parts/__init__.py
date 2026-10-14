"""Generare procedurală de piese pentru datasetul de inspecție vizuală.

Modulul produce roțile dințate ale celulei CNC — o familie de referință plus familiile mai
mici care intră în prinzător — împreună cu eticheta lor. Geometria și eticheta ies din
aceeași decizie, luată înainte de randare, deci nu se pot dezsincroniza.

Vezi :mod:`cncdt.cnc_cell.parts.gear` pentru familii, parametri și catalogul de defecte și
:mod:`cncdt.cnc_cell.parts.manifest` pentru formatul de etichete.
"""

from cncdt.cnc_cell.parts.gear import (
    DEFAULT_FAMILY,
    DEFECT_CLASSES,
    FAMILIES,
    FAMILY_BY_NAME,
    NOMINAL,
    GearParams,
    PartFamily,
    PartVariant,
    build_mesh,
    build_variant,
    build_variant_from_params,
    family_of,
)
from cncdt.cnc_cell.parts.manifest import LabelRecord, read_manifest, write_manifest

__all__ = [
    "DEFAULT_FAMILY",
    "DEFECT_CLASSES",
    "FAMILIES",
    "FAMILY_BY_NAME",
    "GearParams",
    "LabelRecord",
    "NOMINAL",
    "PartFamily",
    "PartVariant",
    "build_mesh",
    "build_variant",
    "build_variant_from_params",
    "family_of",
    "read_manifest",
    "write_manifest",
]
