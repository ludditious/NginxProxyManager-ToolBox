# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import requests


class NpmError(Exception):
    pass


class NpmClient:
    """Minimal Nginx Proxy Manager REST client (JWT bearer)."""

    _API_EXPORTS: tuple[tuple[str, str], ...] = (
        ("proxy-hosts", "/api/nginx/proxy-hosts"),
        ("redirection-hosts", "/api/nginx/redirection-hosts"),
        ("dead-hosts", "/api/nginx/dead-hosts"),
        ("streams", "/api/nginx/streams"),
        ("access-lists", "/api/access-lists"),
        ("certificates", "/api/nginx/certificates"),
        ("settings", "/api/settings"),
        ("users", "/api/users"),
    )

    def __init__(
        self,
        base_url: str,
        *,
        identity: str,
        secret: str,
        verify_tls: bool = False,
        timeout: int = 120,
    ) -> None:
        self.base_url = self.normalize_api_url(base_url)
        self.identity = identity.strip()
        self.secret = secret
        self.verify_tls = bool(verify_tls)
        self.timeout = timeout
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "NginxProxyManager-ToolBox/1.0"})

    def _request_verify(self) -> bool:
        """Only validate certificates when the user explicitly enabled Verify TLS."""
        return self.verify_tls

    def login(self) -> None:
        url = urljoin(self.base_url + "/", "api/tokens")
        resp = self._session.post(
            url,
            json={"identity": self.identity, "secret": self.secret},
            timeout=self.timeout,
            verify=self._request_verify(),
        )
        if resp.status_code >= 400:
            raise NpmError(f"Authentication failed ({resp.status_code}): {resp.text[:500]}")
        data = resp.json()
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise NpmError("Authentication response did not include a token.")
        self._token = str(token)
        self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _get_json(self, path: str) -> Any:
        if not self._token:
            self.login()
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        resp = self._session.get(url, timeout=self.timeout, verify=self._request_verify())
        if resp.status_code == 401:
            self._token = None
            self.login()
            resp = self._session.get(url, timeout=self.timeout, verify=self._request_verify())
        if resp.status_code >= 400:
            raise NpmError(f"GET {path} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def probe_api(self) -> bool:
        """Return True if this host looks like NPM (tokens endpoint exists)."""
        url = urljoin(self.base_url + "/", "api/tokens")
        try:
            resp = self._session.post(
                url,
                json={"identity": "__probe__", "secret": "__probe__"},
                timeout=min(15, self.timeout),
                verify=self._request_verify(),
            )
        except requests.RequestException:
            return False
        if resp.status_code in (400, 401, 403, 422):
            return True
        ctype = (resp.headers.get("content-type") or "").lower()
        return "json" in ctype and resp.status_code < 500

    def export_configuration(self) -> dict[str, Any]:
        out: dict[str, Any] = {"api_base_url": self.base_url}
        for key, path in self._API_EXPORTS:
            try:
                out[key] = self._get_json(path)
            except NpmError as e:
                out[key] = {"_error": str(e)}
        return out

    @staticmethod
    def normalize_api_url(url: str) -> str:
        """Preserve http vs https; default to http when no scheme (never assume TLS)."""
        raw = (url or "").strip()
        if not raw:
            return ""
        if not raw.startswith(("http://", "https://")):
            raw = "http://" + raw
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

