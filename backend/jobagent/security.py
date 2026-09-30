from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import keyring

SERVICE = "com.meridian.jobagent"


class SecretStore:
    """Only an OS protected credential vault is an acceptable persistent backend."""

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()

    def _backend(self):
        backend = keyring.get_keyring()
        name = type(backend).__module__
        if not name.startswith("keyring.backends.macOS"):
            raise RuntimeError("macOS Keychain is required for storing credentials. No plaintext fallback is used.")
        return backend

    def get(self, name: str) -> str | None:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            value = self._backend().get_password(SERVICE, name)
            if value is not None:
                self._cache[name] = value
            return value

    def set(self, name: str, value: str):
        with self._lock:
            self._backend().set_password(SERVICE, name, value)
            self._cache[name] = value

    def delete(self, name: str):
        with self._lock:
            backend = self._backend()
            if backend.get_password(SERVICE, name):
                backend.delete_password(SERVICE, name)
            self._cache.pop(name, None)


class RedactingFilter(logging.Filter):
    def filter(self, record):
        text = record.getMessage()
        text = re.sub(r"(?i)(bearer\s+)[^\s,]+", r"\1[redacted]", text)
        text = re.sub(
            r"(?i)((?:access_token|refresh_token|api_key|password|authorization)[\s=:]+)[^\s,]+", r"\1[redacted]", text
        )
        record.msg, record.args = text, ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                "logger": record.name,
            },
            ensure_ascii=False,
        )


def public_url(url: str, allow_local: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A public http(s) URL without credentials is required")
    host = parsed.hostname.lower()
    if allow_local and host in ("localhost", "127.0.0.1", "::1"):
        return url
    try:
        addresses = {x[4][0] for x in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("Could not resolve the URL hostname") from exc
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Private network and local system URLs are not permitted")
    return url


async def safe_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """Validate every redirect destination, including redirects into local services."""
    for _ in range(6):
        public_url(url)
        response = await client.get(url, follow_redirects=False, **kwargs)
        if response.is_redirect:
            url = str(response.url.join(response.headers["location"]))
            continue
        response.raise_for_status()
        return response
    raise ValueError("Too many redirects")
