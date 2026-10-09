# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .dns_resolve import is_literal_ip, resolve_hostname
from .http_sni import SNIHTTPSAdapter
from .npm_errors import friendly_auth_failure, sni_hostname_from_host_header


class NpmError(Exception):
    pass


class NpmClient:
    """Minimal Nginx Proxy Manager REST client (JWT bearer)."""

    _API_EXPORTS: tuple[tuple[str, str], ...] = (
        ("proxy-hosts", "/api/nginx/proxy-hosts"),
        ("redirection-hosts", "/api/nginx/redirection-hosts"),
        ("dead-hosts", "/api/nginx/dead-hosts"),
        ("streams", "/api/nginx/streams"),
        ("access-lists", "/api/nginx/access-lists"),
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
        dns_servers: list[str] | None = None,
        host_overrides: dict[str, str] | None = None,
        admin_host: str | None = None,
    ) -> None:
        self.base_url = self.normalize_api_url(base_url)
        self.identity = identity.strip()
        self.secret = secret
        self.verify_tls = bool(verify_tls)
        self.timeout = timeout
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "NginxProxyManager-ToolBox/1.0"})
        self._request_root, self._http_host = self._connection_target(
            self.base_url,
            dns_servers,
            host_overrides=host_overrides,
            admin_host=admin_host,
        )
        self._configure_https_sni()

    def _request_verify(self) -> bool:
        """Only validate certificates when the user explicitly enabled Verify TLS."""
        return self.verify_tls

    @staticmethod
    def _connection_target(
        base_url: str,
        dns_servers: list[str] | None,
        *,
        host_overrides: dict[str, str] | None = None,
        admin_host: str | None = None,
    ) -> tuple[str, str | None]:
        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        scheme = parsed.scheme or "http"
        port = parsed.port or (443 if scheme == "https" else 80)
        logical = (admin_host or host).strip()
        logical_key = logical.lower()
        if host_overrides and logical_key in host_overrides:
            connect = host_overrides[logical_key]
            root = f"{scheme}://{connect}:{port}".rstrip("/")
            http_host = logical if port in (80, 443) else f"{logical}:{port}"
            return root, http_host
        admin = (admin_host or "").strip()
        if admin and host and admin.lower() != host.lower() and not is_literal_ip(admin):
            root = base_url.rstrip("/")
            http_host = admin if port in (80, 443) else f"{admin}:{port}"
            return root, http_host
        if not host or is_literal_ip(host):
            return base_url.rstrip("/"), None
        try:
            ip = resolve_hostname(host, dns_servers=dns_servers)
        except (OSError, ValueError, RuntimeError) as e:
            raise OSError(str(e)) from e
        root = f"{scheme}://{ip}:{port}".rstrip("/")
        http_host = host if port in (80, 443) else f"{host}:{port}"
        return root, http_host

    def _configure_https_sni(self) -> None:
        parsed = urlparse(self._request_root)
        if parsed.scheme != "https" or not self._http_host:
            return
        sni = sni_hostname_from_host_header(self._http_host)
        tcp_host = parsed.hostname or ""
        if not sni or sni.lower() == tcp_host.lower():
            return
        self._session.mount("https://", SNIHTTPSAdapter(sni))

    def _extra_headers(self) -> dict[str, str]:
        if not self._http_host:
            return {}
        return {"Host": self._http_host}

    def login(self) -> None:
        url = urljoin(self._request_root + "/", "api/tokens")
        resp = self._session.post(
            url,
            json={"identity": self.identity, "secret": self.secret},
            timeout=self.timeout,
            verify=self._request_verify(),
            headers=self._extra_headers(),
        )
        if resp.status_code >= 400:
            raise NpmError(friendly_auth_failure(resp.status_code, resp.text))
        data = resp.json()
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise NpmError("Authentication response did not include a token.")
        self._token = str(token)
        self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _get_json(self, path: str) -> Any:
        if not self._token:
            self.login()
        url = urljoin(self._request_root + "/", path.lstrip("/"))
        hdrs = self._extra_headers()
        resp = self._session.get(url, timeout=self.timeout, verify=self._request_verify(), headers=hdrs)
        if resp.status_code == 401:
            self._token = None
            self.login()
            resp = self._session.get(url, timeout=self.timeout, verify=self._request_verify(), headers=hdrs)
        if resp.status_code >= 400:
            raise NpmError(f"GET {path} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def _request_json(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> Any:
        if not self._token:
            self.login()
        url = urljoin(self._request_root + "/", path.lstrip("/"))
        hdrs = self._extra_headers()
        resp = self._session.request(
            method,
            url,
            json=json_body,
            timeout=self.timeout,
            verify=self._request_verify(),
            headers=hdrs,
        )
        if resp.status_code == 401:
            self._token = None
            self.login()
            resp = self._session.request(
                method,
                url,
                json=json_body,
                timeout=self.timeout,
                verify=self._request_verify(),
                headers=hdrs,
            )
        if resp.status_code >= 400:
            raise NpmError(
                f"{method} {path} failed ({resp.status_code}): {resp.text[:500]}"
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _post_json(self, path: str, body: dict) -> Any:
        return self._request_json("POST", path, json_body=body)

    def _put_json(self, path: str, body: dict) -> Any:
        return self._request_json("PUT", path, json_body=body)

    def _delete_json(self, path: str) -> None:
        self._request_json("DELETE", path)

    def probe_api(self) -> bool:
        """Return True if this host looks like NPM (tokens endpoint exists)."""
        url = urljoin(self._request_root + "/", "api/tokens")
        try:
            resp = self._session.post(
                url,
                json={"identity": "__probe__", "secret": "__probe__"},
                timeout=min(15, self.timeout),
                verify=self._request_verify(),
                headers=self._extra_headers(),
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
                if key == "access-lists":
                    out[key] = self._get_json(f"{path}?expand=clients")
                else:
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

