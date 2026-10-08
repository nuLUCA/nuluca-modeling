"""Small, explicit local canvas surface for a member's own Rhino process."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .bridge import BridgeClient, BridgeError

_READ = {
    "canvas_status": "/status",
    "canvas_errors": "/errors",
    "canvas_solution_state": "/solution-state",
    "canvas_get_values": "/get-values",
    "dialogs_list": "/dialogs",
    "viewport_zoom_extents": "/zoom-extents",
}
_WRITE = {
    "canvas_new": "/new",
    "canvas_recompute": "/recompute",
    "canvas_set_slider": "/set-slider",
    "canvas_set_panel": "/set-panel",
    "canvas_enable": "/enable",
    "canvas_set_preview": "/set-preview",
    "canvas_remove_connection": "/remove-connection",
    "canvas_bake": "/bake",
    "viewport_set_camera": "/set-camera",
    "viewport_set_display_mode": "/set-display-mode",
    "dialog_dismiss": "/dismiss-dialog",
}
_FILE = {"canvas_import", "canvas_export", "canvas_save", "viewport_screenshot"}
_REQUIRED = {
    "canvas_set_slider": {"nickname": "string", "value": "number"},
    "canvas_set_panel": {"nickname": "string", "value": "string"},
    "canvas_enable": {"nickname": "string", "enabled": "boolean"},
    "canvas_set_preview": {"nickname": "string", "preview": "boolean"},
    "canvas_import": {"file": "string"},
    "canvas_export": {"file": "string"},
    "canvas_save": {"file": "string"},
    "viewport_screenshot": {"file": "string"},
    "viewport_set_display_mode": {"mode": "string"},
}


def local_tools() -> list[dict]:
    tools = []
    for name in (*_READ, *_WRITE, *_FILE):
        fields = _REQUIRED.get(name, {})
        tools.append({
            "name": name,
            "description": name.replace("_", " ").capitalize() + " on the local Rhino canvas.",
            "inputSchema": {"type": "object", "properties": {
                key: {"type": kind} for key, kind in fields.items()
            }, "required": list(fields), "additionalProperties": True},
        })
    return tools


class LocalTools:
    def __init__(self, bridge: BridgeClient | None = None, *, root: Path | None = None) -> None:
        self.bridge = bridge or BridgeClient()
        self.root = (root or Path(os.environ.get("NULUCA_MODELING_WORK_ROOT", "~/nuluca-modeling/work"))).expanduser().resolve()

    def _file(self, value: object, suffix: str) -> Path:
        if not isinstance(value, str) or not value or not value.endswith(suffix):
            raise BridgeError(f"A {suffix} file under the modeling work directory is required.")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise BridgeError("The file must stay inside the modeling work directory.")
        return path

    async def call(self, name: str, args: dict) -> dict:
        if not isinstance(args, dict):
            raise BridgeError("Tool arguments must be an object.")
        if name in _READ:
            return await self.bridge.call("GET", _READ[name])
        if name in _WRITE:
            return await self.bridge.call("POST", _WRITE[name], body=args)
        if name == "canvas_import":
            path = self._file(args.get("file"), ".json")
            if not path.is_file() or path.stat().st_size > 512 * 1024:
                raise BridgeError("The definition file is missing or too large.")
            try:
                definition = json.loads(path.read_text())
            except ValueError as exc:
                raise BridgeError("The definition file is invalid JSON.") from exc
            if not isinstance(definition, dict):
                raise BridgeError("The definition must be a JSON object.")
            return await self.bridge.call("POST", "/import", body=definition,
                                          query={"clear": "true"})
        if name == "canvas_export":
            path = self._file(args.get("file"), ".json")
            result = await self.bridge.call("GET", "/export")
            if result.get("success") and isinstance(result.get("script"), dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result["script"], indent=2) + "\n")
                return {"success": True, "file": str(path)}
            return result
        if name in {"canvas_save", "viewport_screenshot"}:
            suffix = ".ghx" if name == "canvas_save" else ".png"
            path = self._file(args.get("file"), suffix)
            staged = self.bridge.root / "files" / f"member-{uuid.uuid4().hex}{suffix}"
            if name == "canvas_save":
                result = await self.bridge.call("POST", "/save", body={"file_path": str(staged)})
            else:
                result = await self.bridge.call("GET", "/screenshot", query={"path": str(staged)})
            if result.get("success"):
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged, path)
                staged.unlink(missing_ok=True)
                return {"success": True, "file": str(path)}
            staged.unlink(missing_ok=True)
            return result
        raise BridgeError("Unknown local canvas tool.")
