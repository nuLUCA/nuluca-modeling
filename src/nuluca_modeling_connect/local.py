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
    "canvas_plugins": "/plugins",
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
_FILE = {"canvas_preflight", "canvas_import", "canvas_import_scripts", "canvas_export", "canvas_save",
         "viewport_screenshot"}
_REQUIRED = {
    "canvas_set_slider": {"nickname": "string", "value": "number"},
    "canvas_set_panel": {"nickname": "string", "value": "string"},
    "canvas_enable": {"nickname": "string", "enabled": "boolean"},
    "canvas_set_preview": {"nickname": "string", "preview": "boolean"},
    "canvas_preflight": {"file": "string"},
    "canvas_import": {"file": "string"},
    "canvas_import_scripts": {"file": "string"},
    "canvas_export": {"file": "string"},
    "canvas_save": {"file": "string"},
    "viewport_screenshot": {"file": "string"},
    "viewport_set_display_mode": {"mode": "string"},
}
_IMPORT_CHOICES = {
    "replace_canvas": {"type": "boolean", "description": "Clear a canvas that has objects on it. Set this only after the member agrees."},
    "add_to_canvas": {"type": "boolean", "description": "Keep the objects on the canvas and add this definition to them."},
}
_OPTIONAL = {"canvas_import": _IMPORT_CHOICES, "canvas_import_scripts": _IMPORT_CHOICES}
_DESCRIPTIONS = {
    "canvas_plugins": "List the third-party Grasshopper plugins installed on the member's machine.",
    "canvas_preflight": "Check which plugins and scripts a local definition has, before canvas_import. Changes nothing.",
    "canvas_import": ("Import a local definition with no scripts onto the Rhino canvas. A definition with scripts "
                      "returns definition_has_scripts and changes nothing; use canvas_import_scripts. If the canvas has "
                      "objects, this returns canvas_not_empty and changes nothing; ask the member, then repeat with "
                      "replace_canvas or add_to_canvas."),
    "canvas_import_scripts": ("Import a local definition that carries scripts. The scripts run on the member's computer "
                              "when it solves, outside your sandbox. Tell the member what they do first, and get their "
                              "approval for a definition that came from someone else. canvas_not_empty applies here too."),
}
_NOT_EMPTY = ("The canvas has objects on it, and importing would clear them. Ask the member, then repeat with "
              "replace_canvas true to clear them, or add_to_canvas true to keep them. Nothing has changed.")
_HAS_SCRIPTS = ("This definition runs code when it solves, and Rhino runs it outside your sandbox. Tell the member "
                "what the scripts do. If the definition came from someone else, get their approval first. Then import "
                "it with canvas_import_scripts. Nothing has changed.")
_SCRIPT_FIELDS = {"ScriptSource", "ScriptCode", "ScriptUsingCode", "ScriptAdditionalCode", "UsingCode", "AdditionalCode"}
_NEEDS_UPDATE = "This plugin cannot check definitions yet. Run setup again to update it, or use canvas_import."


def local_tools() -> list[dict]:
    tools = []
    for name in (*_READ, *_WRITE, *_FILE):
        fields = _REQUIRED.get(name, {})
        tools.append({
            "name": name,
            "description": _DESCRIPTIONS.get(name, name.replace("_", " ").capitalize() + " on the local Rhino canvas."),
            "inputSchema": {"type": "object", "properties": {
                **{key: {"type": kind} for key, kind in fields.items()}, **_OPTIONAL.get(name, {}),
            }, "required": list(fields), "additionalProperties": True},
        })
    return tools


def _label(node: dict) -> str:
    return str(node.get("NickName") or node.get("Name") or node.get("Type") or "unnamed component")


def script_report(definition: dict) -> dict:
    """Name the components that carry source, and the cluster bodies that cannot be read here."""
    scripts: dict[int, str] = {}
    opaque: dict[int, str] = {}

    def visit(node: object, owner: dict | None) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, owner)
        elif isinstance(node, dict):
            owner = node if "Type" in node else owner
            for key, value in node.items():
                filled = isinstance(value, str) and bool(value.strip())
                if key in _SCRIPT_FIELDS and filled:
                    scripts[id(owner or node)] = _label(owner or node)
                elif key == "NativeDocument" and filled:
                    opaque[id(owner or node)] = _label(owner or node)
                else:
                    visit(value, owner)

    visit(definition, None)
    return {"script_count": len(scripts), "script_components": sorted(scripts.values()),
            "unreadable_cluster_bodies": sorted(opaque.values())}


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

    def _definition(self, value: object) -> dict:
        path = self._file(value, ".json")
        if not path.is_file() or path.stat().st_size > 512 * 1024:
            raise BridgeError("The definition file is missing or too large.")
        try:
            definition = json.loads(path.read_text())
        except ValueError as exc:
            raise BridgeError("The definition file is invalid JSON.") from exc
        if not isinstance(definition, dict):
            raise BridgeError("The definition must be a JSON object.")
        return definition

    async def _preflight(self, definition: dict) -> dict:
        scripts = script_report(definition)
        try:
            return {**await self.bridge.call("POST", "/preflight", body=definition), **scripts}
        except BridgeError as exc:
            if "(404)" in str(exc):
                return {"success": False, "error": "preflight_unavailable", "message": _NEEDS_UPDATE, **scripts}
            raise

    async def call(self, name: str, args: dict) -> dict:
        if not isinstance(args, dict):
            raise BridgeError("Tool arguments must be an object.")
        if name in _READ:
            return await self.bridge.call("GET", _READ[name])
        if name in _WRITE:
            return await self.bridge.call("POST", _WRITE[name], body=args)
        if name in {"canvas_import", "canvas_import_scripts", "canvas_preflight"}:
            definition = self._definition(args.get("file"))
            if name == "canvas_preflight":
                return await self._preflight(definition)
            scripts = script_report(definition)
            if name == "canvas_import" and (scripts["script_count"] or scripts["unreadable_cluster_bodies"]):
                return {"success": False, "error": "definition_has_scripts", "message": _HAS_SCRIPTS, **scripts}
            add = args.get("add_to_canvas") is True
            if not add and args.get("replace_canvas") is not True:
                status = await self.bridge.call("GET", "/status")
                objects = int(status.get("component_count") or 0) + int(status.get("parameter_count") or 0)
                if objects:
                    return {"success": False, "warning": "canvas_not_empty", "object_count": objects,
                            "message": _NOT_EMPTY}
            return await self.bridge.call("POST", "/import", body=definition,
                                          query={"clear": "false" if add else "true"})
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
