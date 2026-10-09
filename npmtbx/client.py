# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .dns_resolve import is_literal_ip, resolve_hostname
from .http_sni import SNIHTTPSAdapter
from .npm_errors import friendly_auth_failure, sni_hostname_from_host_header


class NpmError(Exception):
    pass


@dataclass(frozen=True)
class CertificateUploadMaterial:
    certificate_pem: str
    key_pem: str
    intermediate_pem: str | None = None


def _format_api_error(
    method: str,
    path: str,
    status: int,
    body_text: str,
    json_body: dict | None,
) -> str:
    msg = f"{method} {path} failed ({status}): {(body_text or '')[:500]}"
    if not isinstance(json_body, dict):
        return msg
    keys = sorted(json_body.keys())
    if keys:
        msg += f" | sent: {', '.join(keys)}"
    domains = json_body.get("domain_names")
    if domains:
        msg += f" | domains={domains!r}"
    name = json_body.get("nice_name") or json_body.get("name")
    if name:
        msg += f" | name={name!r}"
    return msg


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
                _format_api_error(method, path, resp.status_code, resp.text, json_body)
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _post_json(self, path: str, body: dict) -> Any:
        return self._request_json("POST", path, json_body=body)

    def upload_certificate_pem(
        self,
        cert_id: int,
        *,
        certificate_pem: str,
        key_pem: str,
        intermediate_pem: str | None = None,
    ) -> Any:
        """POST /api/nginx/certificates/{id}/upload (provider must be other)."""
        if not self._token:
            self.login()
        path = f"/api/nginx/certificates/{int(cert_id)}/upload"
        url = urljoin(self._request_root + "/", path.lstrip("/"))
        hdrs = self._extra_headers()
        files: dict[str, tuple[str, bytes, str]] = {
            "certificate": (
                "certificate.pem",
                certificate_pem.encode("utf-8"),
                "application/x-pem-file",
            ),
            "certificate_key": (
                "privkey.pem",
                key_pem.encode("utf-8"),
                "application/x-pem-file",
            ),
        }
        if intermediate_pem and intermediate_pem.strip():
            files["intermediate_certificate"] = (
                "chain.pem",
                intermediate_pem.encode("utf-8"),
                "application/x-pem-file",
            )
        resp = self._session.post(
            url,
            files=files,
            timeout=self.timeout,
            verify=self._request_verify(),
            headers=hdrs,
        )
        if resp.status_code == 401:
            self._token = None
            self.login()
            resp = self._session.post(
                url,
                files=files,
                timeout=self.timeout,
                verify=self._request_verify(),
                headers=hdrs,
            )
        if resp.status_code >= 400:
            raise NpmError(
                _format_api_error("POST", path, resp.status_code, resp.text, None)
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _put_json(self, path: str, body: dict) -> Any:
        return self._request_json("PUT", path, json_body=body)

    def _delete_json(self, path: str) -> None:
        self._request_json("DELETE", path)

    @staticmethod
    def _material_from_zip(data: bytes) -> CertificateUploadMaterial | None:
        if not data or not data.startswith(b"PK"):
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                by_name: dict[str, str] = {}
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    base = PurePosixPath(name).name.lower()
                    if not base.endswith(".pem"):
                        continue
                    text = zf.read(name).decode("utf-8", errors="replace").strip()
                    if "BEGIN" in text:
                        by_name[base] = text
                key = by_name.get("privkey.pem")
                if not key:
                    return None
                chain = by_name.get("fullchain.pem")
                intermediate = by_name.get("chain.pem")
                cert_only = by_name.get("cert.pem")
                if chain:
                    cert_body = chain
                    inter = intermediate if intermediate and intermediate not in chain else None
                elif cert_only:
                    cert_body = cert_only
                    inter = intermediate
                    if inter:
                        cert_body = cert_only
                else:
                    return None
                if "BEGIN CERTIFICATE" not in cert_body:
                    return None
                return CertificateUploadMaterial(
                    certificate_pem=cert_body,
                    key_pem=key,
                    intermediate_pem=inter,
                )
        except (zipfile.BadZipFile, OSError, KeyError):
            return None

    def download_certificate_materials(
        self, cert_id: int
    ) -> tuple[CertificateUploadMaterial | None, str | None]:
        """
        GET /api/nginx/certificates/{id}/download — NPM zips PEMs on the Source host.
        """
        if not self._token:
            self.login()
        path = f"/api/nginx/certificates/{int(cert_id)}/download"
        url = urljoin(self._request_root + "/", path.lstrip("/"))
        hdrs = {**self._extra_headers(), "Accept": "application/zip, application/octet-stream, */*"}
        try:
            resp = self._session.get(
                url,
                timeout=max(self.timeout, 180),
                verify=self._request_verify(),
                headers=hdrs,
            )
        except requests.RequestException as exc:
            return None, f"download request failed: {exc}"
        if resp.status_code == 401:
            self._token = None
            self.login()
            try:
                resp = self._session.get(
                    url,
                    timeout=max(self.timeout, 180),
                    verify=self._request_verify(),
                    headers=hdrs,
                )
            except requests.RequestException as exc:
                return None, f"download request failed: {exc}"
        if resp.status_code >= 400:
            body = (resp.text or "")[:300]
            return None, f"download HTTP {resp.status_code}: {body}"
        content = resp.content or b""
        material = self._material_from_zip(content)
        if material:
            return material, None
        ctype = resp.headers.get("content-type", "")
        if content[:1] == b"{":
            return None, f"download returned JSON not zip: {content[:300]!r}"
        return None, (
            f"download returned unexpected data (content-type={ctype!r}, "
            f"{len(content)} bytes, zip PEMs missing)"
        )

    def download_letsencrypt_pem(self, cert_id: int) -> tuple[str, str] | None:
        material, _err = self.download_certificate_materials(cert_id)
        if not material:
            return None
        return material.certificate_pem, material.key_pem

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

    def _export_certificates(self) -> list[Any]:
        """List certificates, then GET each row so meta (email, DNS flags) is populated."""
        data = self._get_json("/api/nginx/certificates")
        if not isinstance(data, list):
            return data if data is not None else []
        detailed: list[Any] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            cid = row.get("id")
            if cid is None:
                detailed.append(row)
                continue
            try:
                detailed.append(
                    self._get_json(f"/api/nginx/certificates/{int(cid)}")
                )
            except NpmError:
                detailed.append(row)
        return detailed

    def export_configuration(self) -> dict[str, Any]:
        out: dict[str, Any] = {"api_base_url": self.base_url}
        for key, path in self._API_EXPORTS:
            try:
                if key == "access-lists":
                    out[key] = self._get_json(f"{path}?expand=clients")
                elif key == "certificates":
                    out[key] = self._export_certificates()
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

