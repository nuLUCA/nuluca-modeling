"""Catalog-driven, product-key authenticated cloud client."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx


class ConnectError(Exception):
    """A bounded error safe to show to a connected agent."""


class CloudClient:
    def __init__(self, *, base_url: str | None = None, key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url or os.getenv("NULUCA_AGENT_CONNECT_URL") or "").rstrip("/")
        self.key = key or os.getenv("NULUCA_AGENT_CONNECT_KEY") or ""
        origin = urlsplit(self.base_url)
        if origin.scheme != "https" or not origin.netloc or origin.username or origin.password:
            raise ConnectError("A secure nuLUCA connection URL is required.")
        if not self.key.startswith(("ae_test_", "ae_live_")):
            raise ConnectError("A nuLUCA member key is required.")
        self._http = httpx.AsyncClient(
            transport=transport, timeout=20, follow_redirects=False,
            headers={"X-API-Key": self.key, "Accept": "application/json"},
        )
        self._operations: dict[str, dict] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def discover(self) -> list[dict]:
        response = await self._request("GET", "/v1/agent-connect/capabilities")
        if response.get("product") != "nuluca" or response.get("auth", {}).get("owner_type") != "person":
            raise ConnectError("The member catalog is unavailable.")
        operations = {}
        for item in response.get("capabilities", []):
            if not isinstance(item, dict) or item.get("status") != "active":
                continue
            name, path = item.get("mcp_tool_name"), item.get("path")
            if (isinstance(name, str) and name.startswith(("gh_", "biomimicry_", "projects_", "guide_"))
                    and isinstance(path, str) and path.startswith("/v1/agent-connect/nuluca/")
                    and item.get("method") == "POST" and isinstance(item.get("request_schema"), dict)):
                operations[name] = item
        self._operations = operations
        return list(operations.values())

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in self._operations:
            raise ConnectError("Tool is not granted to this connection.")
        if not isinstance(arguments, dict):
            raise ConnectError("Tool arguments must be an object.")
        descriptor = self._operations[name]
        attempt_dir = os.getenv("NULUCA_STAGING_ATTEMPT_DIR")
        if attempt_dir:
            path = Path(attempt_dir)
            if not path.is_dir():
                raise ConnectError("The staging attempt directory is unavailable.")
            marker = path / f"{uuid.uuid4().hex}.json"
            try:
                with marker.open("x") as stream:
                    json.dump({"tool": name, "at": datetime.now(UTC).isoformat()}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise ConnectError("The staging attempt could not be recorded.") from exc
        return await self._request("POST", descriptor["path"], json=arguments)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        # Descriptors may supply paths, never hosts or traversal outside C9.
        if not path.startswith("/v1/agent-connect/") or ".." in path or "//" in path:
            raise ConnectError("Tool path is invalid.")
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        if urlsplit(url).netloc != urlsplit(self.base_url).netloc:
            raise ConnectError("Tool host is invalid.")
        try:
            response = await self._http.request(method, url, **kwargs)
            if response.status_code in {301, 302, 303, 307, 308}:
                raise ConnectError("The nuLUCA endpoint redirected unexpectedly.")
            if response.status_code >= 400:
                member_errors = {
                    401: "Member key rejected. Create a new key in Connected agents and reconnect.",
                    402: "Not enough credit for this nuLUCA tool.",
                    429: "This connection is temporarily rate limited.",
                    503: "The nuLUCA service is temporarily unavailable.",
                }
                raise ConnectError(member_errors.get(response.status_code,
                                                    f"nuLUCA request failed ({response.status_code})."))
            body = response.json()
        except ConnectError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectError("The nuLUCA service could not be reached.") from exc
        if not isinstance(body, dict):
            raise ConnectError("The nuLUCA response is invalid.")
        return body
