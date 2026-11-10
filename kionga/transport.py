"""Bearer tokens never cross a network in cleartext by accident.

A token may be sent over plain ``http://`` only to a host that cannot be reached
across an untrusted network:

- loopback: ``localhost`` (and ``*.localhost``), ``127.0.0.0/8``, ``::1``;
- a container or cluster service name: a single-label host such as ``gateway``
  (Compose service names), or a Kubernetes name ending in ``.svc`` or
  ``.svc.cluster.local``. These resolve only inside the container network.

Every other ``http://`` URL with a token raises :class:`InsecureTransportError`,
including private-network addresses such as ``192.168.1.20``. To send a token
over plaintext on purpose, pass ``allow_insecure_http=True`` or set
``KIONGA_ALLOW_INSECURE_HTTP=1``; a warning is logged each time.
"""

from __future__ import annotations

import ipaddress
import logging
import os

import httpx

logger = logging.getLogger("kionga")

ALLOW_ENV = "KIONGA_ALLOW_INSECURE_HTTP"
_TRUE = {"1", "true", "yes", "on"}
_CLUSTER_SUFFIXES = (".svc", ".svc.cluster.local")


class InsecureTransportError(ValueError):
    """A bearer token would be sent over plaintext HTTP to a non-local host."""


def insecure_http_allowed(explicit: bool | None = None) -> bool:
    """The caller's explicit choice, else ``KIONGA_ALLOW_INSECURE_HTTP``."""
    if explicit is not None:
        return explicit
    return os.environ.get(ALLOW_ENV, "").strip().lower() in _TRUE


def is_local_host(host: str) -> bool:
    """True for loopback and container/cluster-internal service names."""
    host = host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        mapped = getattr(address, "ipv4_mapped", None)
        return (mapped or address).is_loopback
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if "." not in host:
        return True  # Compose/Kubernetes service name, e.g. "gateway"
    return host.endswith(_CLUSTER_SUFFIXES)


def token_transport_ok(url: str | httpx.URL, *, allow_insecure_http: bool | None = None) -> bool:
    """Whether a bearer token may be sent to ``url``.

    True for HTTPS and for plaintext to a local host. For any other plaintext
    URL: True with a logged warning when insecure HTTP is explicitly allowed,
    otherwise False.
    """
    parsed = httpx.URL(str(url))
    if parsed.scheme == "https" or is_local_host(parsed.host):
        return True
    if insecure_http_allowed(allow_insecure_http):
        logger.warning(
            "sending a bearer token over plaintext HTTP to %s (%s is set); "
            "anyone on the network path can read it",
            parsed.host,
            "allow_insecure_http=True" if allow_insecure_http else ALLOW_ENV,
        )
        return True
    return False


def require_token_transport(url: str | httpx.URL, *, allow_insecure_http: bool | None = None) -> None:
    """Raise :class:`InsecureTransportError` unless a token may go to ``url``."""
    if not token_transport_ok(url, allow_insecure_http=allow_insecure_http):
        host = httpx.URL(str(url)).host
        raise InsecureTransportError(
            f"refusing to send the Kionga token over plaintext HTTP to {host}. "
            "Use an https:// URL, or pass allow_insecure_http=True "
            f"(or set {ALLOW_ENV}=1) if this network is trusted."
        )
