"""Jurnalizare structurată, în JSON, pentru CloudWatch Logs Insights.

Fiecare linie este un obiect JSON cu cheia ``event``. Interogările din dashboard
(``ops/`` și modulul de observabilitate) filtrează pe ``event``, nu pe text liber, deci
mesajele se pot schimba fără să strice interogările.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Formatator minimal: mesajul este deja un dicționar serializabil."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(name: str = "cncdt", level: str | None = None) -> logging.Logger:
    """Configurează rădăcina o singură dată și întoarce loggerul cerut."""
    global _CONFIGURED
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(resolved)
        _CONFIGURED = True
    else:
        logging.getLogger().setLevel(resolved)

    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Scrie un eveniment structurat. ``event`` este cheia de filtrare în Logs Insights."""
    logger.log(level, event, extra={"fields": {"event": event, **fields}})


def note_error(logger: logging.Logger, event: str, error: Exception, **fields: Any) -> None:
    """Eveniment de eroare, cu tipul excepției, fără urmă completă în câmpuri."""
    log_event(
        logger,
        event,
        level=logging.ERROR,
        error_type=type(error).__name__,
        error=str(error),
        **fields,
    )
