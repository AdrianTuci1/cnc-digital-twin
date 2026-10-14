"""Degradare controlată a celulei CNC."""

from .model import (
    MAX_DEGRADATION,
    DefectInjector,
    DegradationState,
    JointFriction,
    ToolWear,
)

__all__ = [
    "MAX_DEGRADATION",
    "DefectInjector",
    "DegradationState",
    "JointFriction",
    "ToolWear",
]
