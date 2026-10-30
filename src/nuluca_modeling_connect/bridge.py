"""Member-side client for the authenticated local JsonToGH bridge."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx


class BridgeError(Exception):
    """A local bridge refusal safe to return to the member's agent."""


def discovery_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "JsonToGH"
    return Path.home() / "Library" / "Application Support" / "JsonToGH"


def _live_pid(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BridgeClient:
    def __init__(self, *, root: Path | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.root = root or discovery_root()
        self.transport = transport

    def _discovery(self) -> dict:
        if not self.root.is_dir():
            raise BridgeError("Start Rhino and open Grasshopper first.")
        if os.name != "nt" and self.root.stat().st_mode & 0o077:
            raise BridgeError("The bridge discovery directory must be private.")
        pinned = os.environ.get("NULUCA_GH_BRIDGE_PORT")
        if pinned and (not pinned.isdecimal() or int(pinned) not in range(6500, 6510)):
            raise BridgeError("The selected bridge port is invalid.")
        files = ([self.root / f"bridge-{pinned}.json"] if pinned else
                 sorted(self.root.glob("bridge-65??.json")))
        found = []
        for path in files:
            if path.is_symlink() or not path.is_file():
                continue
            if os.name != "nt":
                mode = path.stat().st_mode
                if mode & 0o077 or not stat.S_ISREG(mode) or path.stat().st_uid != os.getuid():
                    continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            port = data.get("port")
            if (data.get("protocol") == "jsontogh-bridge/1"
                    and isinstance(port, int) and 6500 <= port <= 6509
                    and path.name == f"bridge-{port}.json"
                    and isinstance(data.get("token"), str) and len(data["token"]) >= 32
                    and _live_pid(data.get("pid"))):
                found.append(data)
        if not found:
            raise BridgeError("No running Rhino bridge is available. Start Rhino and Grasshopper.")
        if len(found) != 1:
            raise BridgeError("Select one running Rhino bridge with NULUCA_GH_BRIDGE_PORT.")
        return found[0]

    async def call(self, method: str, path: str, *, body: dict | None = None,
                   query: dict | None = None) -> dict:
        if method not in {"GET", "POST"} or not path.startswith("/") or "?" in path or ".." in path:
            raise BridgeError("The bridge request is invalid.")
        info = self._discovery()
        port = info["port"]
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         follow_redirects=False, timeout=35) as client:
                response = await client.request(
                    method, f"http://localhost:{port}{path}", params=query,
                    headers={"Authorization": f"Bearer {info['token']}",
                             "Accept": "application/json"},
                    json=body if method == "POST" else None,
                )
            if response.is_redirect:
                raise BridgeError("The local bridge redirected unexpectedly.")
            if response.status_code >= 400:
                raise BridgeError(f"The local bridge refused this request ({response.status_code}).")
            result = response.json()
        except BridgeError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise BridgeError("The local Rhino bridge did not respond.") from exc
        if not isinstance(result, dict):
            raise BridgeError("The local bridge response is invalid.")
        if path == "/status" and (result.get("protocol") != "jsontogh-bridge/1"
                                   or result.get("instance_id") != info.get("instance_id")):
            raise BridgeError("The Rhino bridge changed. Reconnect to the current plugin.")
        return result
