"""Clienți AWS creați leneș.

Importul lui ``boto3`` se face în funcție, nu la nivel de modul: astfel logica pură
(``*_core.py``) poate fi testată local, pe o mașină fără bibliotecile AWS instalate.
"""

from __future__ import annotations

from typing import Any


class AwsUnavailable(RuntimeError):
    """Ridicată când boto3 lipsește sau clientul nu poate fi creat."""


def client(
    service: str,
    region: str | None = None,
    endpoint_url: str | None = None,
    read_timeout: float | None = None,
) -> Any:
    """Întoarce un client boto3, cu eroare clară dacă mediul nu are bibliotecile AWS.

    ``endpoint_url`` este necesar pentru serviciile cu endpoint specific de cont, cum este
    planul de date IoT Core (``<cont>-ats.iot.<regiune>.amazonaws.com``).

    ``read_timeout`` se folosește pentru endpoint-urile serverless: prima invocare plătește
    pornirea containerului, iar timeout-ul implicit de 60 de secunde ar tăia-o.
    """
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover - boto3 există în runtime-ul Lambda
        raise AwsUnavailable(f"boto3 nu este disponibil în acest mediu: {exc}") from exc

    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    if read_timeout is not None:
        from botocore.config import Config  # type: ignore

        # O singură încercare: dacă așteptăm un cold start, o reîncercare ar depăși oricum
        # timeout-ul funcției, iar verdictul are rezerva lui în simulare.
        kwargs["config"] = Config(read_timeout=read_timeout, connect_timeout=10,
                                  retries={"max_attempts": 1})

    try:
        return boto3.client(service, **kwargs)
    except Exception as exc:  # pragma: no cover - depinde de mediul de rulare
        raise AwsUnavailable(f"clientul {service} nu a putut fi creat: {exc}") from exc
