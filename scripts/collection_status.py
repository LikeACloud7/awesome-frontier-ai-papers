"""Per-source collection evidence and bounded retries shared by collectors."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re
import time
import os
from urllib.parse import quote

import requests
from safe_http import RequestPolicyError, pinned_request

ACTIVE_SOURCE: ContextVar[dict | None] = ContextVar("active_collection_source", default=None)
HOST_LOCK = Lock()
HOST_NEXT: dict[str, float] = {}


def safe_message(value) -> str:
    message = str(value)
    for name in ("GITHUB_TOKEN", "OPENALEX_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            message = message.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
    message = re.sub(r"(?i)([?&](?:api_key|apikey|access_token|token|key)=)[^&\s]+", r"\1[redacted]", message)
    return re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer|token|basic)\s+)[^\s]+", r"\1[redacted]", message)[:1000]


def record_source_error(error) -> None:
    status = ACTIVE_SOURCE.get()
    if status is not None:
        message = safe_message(error)
        if message not in status["errors"]:
            status["errors"].append(message)


def record_source_limit(message: str) -> None:
    status = ACTIVE_SOURCE.get()
    if status is not None and message not in status["limits"]:
        status["limits"].append(message)


def collect_with_status(lab: str, name: str, collector):
    status = {"lab": lab, "source": name, "status": "running", "collected": 0,
              "requests": 0, "errors": [], "limits": [],
              "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    token = ACTIVE_SOURCE.set(status)
    started = time.monotonic()
    papers = []
    try:
        papers = collector()
    except Exception as error:
        record_source_error(error)
    finally:
        status["collected"] = len(papers)
        status["elapsed_seconds"] = round(time.monotonic() - started, 2)
        status["status"] = ("partial" if papers else "failed") if status["errors"] else (
            "partial" if status["limits"] else "ok")
        ACTIVE_SOURCE.reset(token)
    return papers, status


def http_request(method: str, url: str, *, retries: int = 3, **kwargs):
    """Retry transport errors/429/5xx; coordinate HF pacing across source threads."""
    host = urlsplit(url).netloc
    headers = {"User-Agent": "awesome-frontier-ai-papers/0.3", **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", 45)
    for attempt in range(retries + 1):
        interval = 1.0 if host == "huggingface.co" else (3.1 if host == "export.arxiv.org" else 0)
        if interval:
            with HOST_LOCK:
                delay = max(0.0, HOST_NEXT.get(host, 0) - time.monotonic())
                HOST_NEXT[host] = time.monotonic() + delay + interval
            if delay:
                time.sleep(delay)
        status = ACTIVE_SOURCE.get()
        if status is not None:
            status["requests"] += 1
        try:
            response = pinned_request(method, url, headers=headers, **kwargs)
            if response.status_code not in {429, 500, 502, 503, 504}:
                return response
            if attempt == retries:
                return response
            try:
                delay = float(response.headers.get("Retry-After", 2 ** attempt))
            except ValueError:
                delay = 2 ** attempt
        except RequestPolicyError:
            raise
        except requests.RequestException:
            if attempt == retries:
                raise
            delay = 2 ** attempt
        time.sleep(min(max(delay, 1), 30))
    raise RuntimeError("HTTP retry loop exhausted")


def http_get(url: str, **kwargs):
    return http_request("GET", url, **kwargs)
