from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Link-local covers the cloud metadata endpoint 169.254.169.254.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),        # unique local
    ipaddress.ip_network("fe80::/10"),       # link-local v6
)

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

# Hosts used to reach the local machine through a name rather than an address.
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


class UnsafeUrlError(ValueError):
    """The URL must not be fetched (SSRF boundary)."""


def _is_blocked_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return True
    return any(address in network for network in _BLOCKED_NETWORKS)


def assert_fetchable_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate a URL before it is fetched, returning it unchanged.

    Article URLs come from Reddit and RSS content, which are influenced by
    whoever posted the link, so the pipeline must not be usable to reach the
    host's own network or a cloud metadata endpoint.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeUrlError("empty URL")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {scheme or '(none)'}")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrlError("URL has no host")
    if host in BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrlError(f"blocked host: {host}")
    if _is_blocked_ip(host):
        raise UnsafeUrlError(f"blocked address: {host}")

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"cannot resolve {host}: {exc}") from exc
        for info in infos:
            resolved = info[4][0]
            if _is_blocked_ip(resolved):
                raise UnsafeUrlError(f"{host} resolves to blocked address {resolved}")

    return raw


def is_fetchable_url(url: str, *, resolve_dns: bool = True) -> bool:
    try:
        assert_fetchable_url(url, resolve_dns=resolve_dns)
    except UnsafeUrlError as exc:
        logger.info("Refusing to fetch %r: %s", url, exc)
        return False
    return True


def assert_redirect_target(url: str, *, resolve_dns: bool = True) -> str:
    """Validate a redirect target, which is attacker-influenced too."""
    return assert_fetchable_url(url, resolve_dns=resolve_dns)


def resolve_and_validate(url: str) -> tuple[str, list[str]]:
    """Validate a URL and return the addresses it resolved to.

    The caller must connect to one of these addresses: validating and then
    letting the transport resolve the name again leaves a window in which the
    second answer can differ from the one that was checked.
    """
    raw = assert_fetchable_url(url, resolve_dns=False)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve {host}: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        resolved = info[4][0]
        if _is_blocked_ip(resolved):
            raise UnsafeUrlError(f"{host} resolves to blocked address {resolved}")
        if resolved not in addresses:
            addresses.append(resolved)
    if not addresses:
        raise UnsafeUrlError(f"{host} did not resolve to any address")
    return raw, addresses
