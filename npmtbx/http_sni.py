# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from requests.adapters import HTTPAdapter


class SNIHTTPSAdapter(HTTPAdapter):
    """HTTPS adapter that sends TLS SNI for a hostname while connecting to another host/IP."""

    def __init__(self, server_hostname: str, **kwargs) -> None:
        self._server_hostname = server_hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs = dict(pool_kwargs)
        pool_kwargs["server_hostname"] = self._server_hostname
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)
