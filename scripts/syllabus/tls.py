"""Verified TLS compatibility for the university syllabus hosts."""

from __future__ import annotations

import ssl

from urllib.parse import urlsplit

import requests

from requests.adapters import HTTPAdapter

class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(proxy, **kwargs)

def mount_verified_legacy_tls(session: requests.Session, url: str) -> None:
    """Opt in to legacy server negotiation on one HTTPS host, retaining verification."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("legacy TLS policy requires an HTTPS site URL")
    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT@SECLEVEL=1")
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    session.mount(f"https://{parsed.netloc}/", _SSLContextAdapter(context))
