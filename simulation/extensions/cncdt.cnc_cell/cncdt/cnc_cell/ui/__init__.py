"""Interfața de operator, desenată cu ``omni.ui``.

Panoul rulează în același proces Kit care produce stream-ul WebRTC, deci operatorul îl vede
în browser fără nicio aplicație separată.
"""

from .panel import ControlPanel

__all__ = ["ControlPanel"]
