"""Small, dependency-free guards shared by collectors and publication tooling."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import unquote, urlsplit


class UnsafeURL(ValueError):
    pass


def public_http_url(value: str) -> str:
    """Validate syntax and literal addresses without making network requests."""
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise UnsafeURL("Missing or oversized URL")
    if re.search(r"[\x00-\x20\x7f\\]", value) or re.search(r"[\x00-\x1f\x7f\\]", unquote(value)):
        raise UnsafeURL("Control characters or backslashes in URL")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (ValueError, UnicodeError) as error:
        raise UnsafeURL("Invalid URL") from error
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise UnsafeURL("Only absolute HTTP(S) URLs are accepted")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("URL credentials are not accepted")
    if port is not None and port not in {80, 443}:
        raise UnsafeURL("Only standard HTTP(S) ports are accepted")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL("Local hosts are not accepted")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise UnsafeURL("A public fully qualified hostname is required")
        if not re.fullmatch(r"[a-z0-9.-]+", hostname) or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", hostname):
            raise UnsafeURL("Non-canonical hostname or IP address")
    else:
        if not global_address(address):
            raise UnsafeURL("Non-public IP address")
    return value


def global_address(address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return global_address(address.ipv4_mapped)
    return bool(address.is_global and not (address.is_multicast or address.is_reserved
                or address.is_loopback or address.is_link_local or address.is_unspecified))


def public_addresses(url: str) -> tuple[str, int, list[str]]:
    parsed = urlsplit(public_http_url(url))
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not global_address(ipaddress.ip_address(ip)) for ip in addresses):
        raise UnsafeURL("DNS resolved to a non-public address")
    return host, port, sorted(addresses, key=lambda ip: ":" in ip)


def safe_link(value: str) -> bool:
    try:
        public_http_url(value)
        return True
    except (UnsafeURL, TypeError):
        return False


def safe_slug(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Invalid lab file identifier")
    return value
