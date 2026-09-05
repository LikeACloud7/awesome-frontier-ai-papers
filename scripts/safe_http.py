"""Read-only HTTP transport: public IP pinning, redirect checks and size limits."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import re
import zlib

import certifi
import requests
from requests.structures import CaseInsensitiveDict
import urllib3

from security import UnsafeURL, public_addresses, public_http_url

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5
SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
SENSITIVE_QUERY = {"api_key", "apikey", "access_token", "token"}


class RequestPolicyError(requests.RequestException):
    """A rejected request must not be retried as a transient network failure."""


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def bounded_body(response, limit: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length:
        if not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > limit:
            raise RequestPolicyError("Invalid Content-Length or download size limit exceeded")
    encoding = response.headers.get("Content-Encoding", "identity").lower()
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    if encoding not in {"identity", "", "gzip"}:
        raise RequestPolicyError("Unsupported response compression")
    output = bytearray()
    wire_size = 0
    for chunk in response.stream(65536, decode_content=False):
        wire_size += len(chunk)
        if wire_size > limit:
            raise RequestPolicyError("Response exceeds the download size limit")
        data = decoder.decompress(chunk, limit - len(output) + 1) if decoder else chunk
        output.extend(data)
        if len(output) > limit:
            raise RequestPolicyError("Decoded response exceeds the download size limit")
    if decoder and not decoder.eof:
        raise RequestPolicyError("Incomplete compressed response")
    return bytes(output)


def pinned_request(method: str, url: str, *, headers=None, params=None, timeout=45,
                   max_bytes=MAX_RESPONSE_BYTES, allow_redirects=True) -> requests.Response:
    if method.upper() not in {"GET", "HEAD"}:
        raise RequestPolicyError("Collectors only support GET and HEAD")
    try:
        public_http_url(url)
    except UnsafeURL as error:
        raise RequestPolicyError(str(error)) from error
    prepared = requests.Request(method.upper(), url, params=params).prepare()
    current = prepared.url
    request_headers = {"Accept-Encoding": "identity", **(headers or {})}
    # Never accept caller-controlled routing/proxy headers.
    request_headers = {k:v for k,v in request_headers.items() if k.lower() not in {"host", "proxy-authorization"}}
    if isinstance(timeout, tuple):
        connect_timeout, read_timeout = timeout
    else:
        connect_timeout = read_timeout = timeout
    if not connect_timeout or not read_timeout:
        raise ValueError("A bounded HTTP timeout is required")
    for hop in range(MAX_REDIRECTS + 1):
        try:
            host, port, addresses = public_addresses(current)
        except UnsafeURL as error:
            raise RequestPolicyError(str(error)) from error
        except OSError as error:
            raise requests.RequestException(str(error)) from error
        parsed = urlsplit(current)
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        routed_headers = {**request_headers, "Host": authority}
        has_secret_query = any(k.lower() in SENSITIVE_QUERY for k, _ in parse_qsl(parsed.query))
        if parsed.scheme != "https" and (has_secret_query or any(k.lower() in SENSITIVE_HEADERS for k in request_headers)):
            raise RequestPolicyError("Credentials require HTTPS")
        # Connect directly to an already validated IP. TLS still authenticates the
        # original hostname; requests cannot perform a second, rebinding DNS lookup.
        pool_type = urllib3.HTTPSConnectionPool if parsed.scheme == "https" else urllib3.HTTPConnectionPool
        tls = {"cert_reqs":"CERT_REQUIRED", "ca_certs":certifi.where(),
               "assert_hostname":host, "server_hostname":host} if parsed.scheme == "https" else {}
        pool = pool_type(addresses[0], port=port, timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout), **tls)
        raw = None
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            raw = pool.urlopen(method.upper(), path, headers=routed_headers, redirect=False, retries=False,
                               preload_content=False, decode_content=False)
            response = requests.Response()
            response.status_code = raw.status
            response.reason = raw.reason
            response.url = current
            response.headers = CaseInsensitiveDict(raw.headers)
            response.encoding = requests.utils.get_encoding_from_headers(response.headers)
            response.request = requests.Request(method, current).prepare()
            location = raw.headers.get("Location")
            if allow_redirects and raw.status in {301,302,303,307,308} and location:
                if hop == MAX_REDIRECTS:
                    raise RequestPolicyError("Collector redirect limit exceeded")
                target = urljoin(current, location)
                public_http_url(target)
                if origin(target) != origin(current):
                    request_headers = {k:v for k,v in request_headers.items() if k.lower() not in SENSITIVE_HEADERS}
                    parts = urlsplit(target)
                    query = [(k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in SENSITIVE_QUERY]
                    target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
                current = target
                continue
            response._content = bounded_body(raw, max_bytes)
            response._content_consumed = True
            return response
        except (UnsafeURL, zlib.error) as error:
            raise RequestPolicyError(str(error)) from error
        except urllib3.exceptions.HTTPError as error:
            raise requests.RequestException(str(error)) from error
        finally:
            if raw is not None:
                raw.close()
            pool.close()
    raise RequestPolicyError("Collector redirect limit exceeded")
