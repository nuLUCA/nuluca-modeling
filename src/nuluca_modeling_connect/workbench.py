"""Incremental, member-owned JsonToGH definition editing."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from .bridge import BridgeError
from .client import CloudClient

_NAMES = ("wb_new", "wb_write", "wb_add_component", "wb_add_parameter", "wb_connect",
          "wb_disconnect", "wb_set_group", "wb_update_node", "wb_remove_node",
          "wb_outline", "wb_read", "wb_summary")


def workbench_tools() -> list[dict]:
    required = {
        "wb_new": ["file"], "wb_write": ["file", "script"],
        "wb_add_component": ["file", "component_type"],
        "wb_add_parameter": ["file", "param_type"],
        "wb_connect": ["file", "source_id", "source_port", "target_id", "target_port"],
        "wb_disconnect": ["file", "target_id", "target_port"],
        "wb_set_group": ["file", "name", "node_ids"],
        "wb_update_node": ["file", "node_id", "updates"],
        "wb_remove_node": ["file", "node_id"],
    }
    descriptions = {
        "wb_write": "Save a complete JsonToGH v1.3 definition in one call; use gh_validate before canvas_import when available.",
        "wb_add_parameter": "Add a Grasshopper parameter. Use Type Number Slider with extra Slider {Value, Min, Max, Type}; a standalone Number uses extra Value (capital V).",
    }
    return [{"name": name, "description": descriptions.get(name,
             name.replace("_", " ").capitalize() + " in your local modeling work directory."),
             "inputSchema": {"type": "object", "properties": {
                 "file": {"type": "string"}, "component_type": {"type": "string"},
                 "param_type": {"type": "string"}, "node_id": {"type": "string"},
                 "source_id": {"type": "string"}, "source_port": {"type": "string"},
                 "target_id": {"type": "string"}, "target_port": {"type": "string"},
                 "name": {"type": "string"}, "nickname": {"type": "string"},
                 "description": {"type": "string"}, "category": {"type": "string"},
                 "subcategory": {"type": "string"},
                 "position": {"type": "object", "properties": {"X": {"type": "number"},
                                                            "Y": {"type": "number"}}},
                 "node_ids": {"type": "array", "items": {"type": "string"}},
                 "updates": {"type": "object", "description": "Exact JsonToGH node fields to update."},
                 "extra": {"type": "object", "description": "Exact JsonToGH parameter fields, including Slider or Value."},
                 "script": {"type": "object", "description": "Complete JsonToGH v1.3 definition with Components, Parameters, Connections and Groups arrays."}},
                 "required": fields, "additionalProperties": True}}
            for name in _NAMES for fields in [required.get(name, ["file"])]]


class Workbench:
    def __init__(self, cloud: CloudClient | None, *, root: Path | None = None) -> None:
        self.cloud = cloud
        self.root = (root or Path(os.environ.get("NULUCA_MODELING_WORK_ROOT", "~/nuluca-modeling/work"))).expanduser().resolve()
        self._spec_cache: dict[tuple[str, str, str], dict] = {}

    def _path(self, file: object) -> Path:
        if not isinstance(file, str) or not file.endswith(".json"):
            raise BridgeError("Choose a .json definition file in the modeling work directory.")
        path = (self.root / file).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise BridgeError("The definition must stay in the modeling work directory.")
        return path

    def _load(self, file: object) -> tuple[Path, dict]:
        path = self._path(file)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise BridgeError("The definition file is missing or invalid.") from exc
        if not isinstance(data, dict) or data.get("Version") != "1.3":
            raise BridgeError("The definition must be JsonToGH version 1.3.")
        return path, data

    @staticmethod
    def _save(path: Path, script: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x") as stream:
                stream.write(json.dumps(script, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _node(script: dict, node_id: str) -> dict:
        for node in script.get("Components", []) + script.get("Parameters", []):
            if node.get("Id") == node_id:
                return node
        raise BridgeError("The named node is absent from this definition.")

    @staticmethod
    def _id(script: dict, proposed: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", proposed.lower()).strip("-") or "node"
        used = {node.get("Id") for node in script.get("Components", []) + script.get("Parameters", [])}
        candidate = base
        number = 2
        while candidate in used:
            candidate = f"{base}-{number}"
            number += 1
        return candidate

    async def call(self, name: str, args: dict) -> dict:
        if name not in _NAMES or not isinstance(args, dict):
            raise BridgeError("Unknown workbench tool or arguments.")
        path = self._path(args.get("file"))
        if name == "wb_write":
            script = args.get("script")
            if (not isinstance(script, dict) or script.get("Version") != "1.3" or
                    any(not isinstance(script.get(key), list) for key in
                        ("Components", "Parameters", "Connections", "Groups"))):
                raise BridgeError("Provide a complete JsonToGH 1.3 definition with four node and wire arrays.")
            if len(json.dumps(script).encode("utf-8")) > 512 * 1024:
                raise BridgeError("The definition exceeds the local bridge import limit.")
            self._save(path, script)
            return {"file": str(path), "components": len(script["Components"]),
                    "parameters": len(script["Parameters"]),
                    "connections": len(script["Connections"])}
        if name == "wb_new":
            if path.exists():
                raise BridgeError("The definition already exists; keep editing it or choose another file.")
            data = {"Version": "1.3", "InputValueMode": "Persistent",
                    "Description": str(args.get("description", "")),
                    "Components": [], "Parameters": [], "Connections": [], "Groups": []}
            self._save(path, data)
            return {"file": str(path), "version": "1.3"}
        path, data = self._load(args["file"])
        if name == "wb_read":
            return data
        if name in {"wb_outline", "wb_summary"}:
            return {"file": str(path), "description": data.get("Description", ""),
                    "components": [{"id": x.get("Id"), "type": x.get("Type"),
                                    "nickname": x.get("NickName")} for x in data["Components"]],
                    "parameters": [{"id": x.get("Id"), "type": x.get("Type"),
                                    "nickname": x.get("NickName")} for x in data["Parameters"]],
                    "connections": data["Connections"], "groups": data["Groups"]}
        if name == "wb_add_component":
            typ = args.get("component_type")
            if not isinstance(typ, str) or not typ.strip():
                raise BridgeError("A catalog component type is required.")
            category, subcategory = str(args.get("category", "")), str(args.get("subcategory", ""))
            cache_key = (typ, category, subcategory)
            spec = self._spec_cache.get(cache_key)
            if spec is None:
                if self.cloud is None:
                    if not category or not subcategory:
                        raise BridgeError("Choose a Grasshopper category and subcategory for this component.")
                    spec = {"category": category, "subcategory": subcategory,
                            "inputs": [], "outputs": []}
                else:
                    response = await self.cloud.call("gh_catalog_spec", {
                        "component_type": typ, **({"category": category} if category else {}),
                        **({"subcategory": subcategory} if subcategory else {})})
                    spec = response.get("component")
                    if not isinstance(spec, dict):
                        raise BridgeError("Choose one exact component from the nuLUCA catalog.")
                if len(self._spec_cache) >= 500:
                    self._spec_cache.pop(next(iter(self._spec_cache)))
                self._spec_cache[cache_key] = spec
            node_id = self._id(data, str(args.get("node_id") or args.get("nickname") or typ))
            node = {"Id": node_id, "Type": typ, "NickName": str(args.get("nickname") or typ),
                    "Category": spec.get("category"), "SubCategory": spec.get("subcategory"),
                    "Position": args.get("position") or {"X": 220 * len(data["Components"]), "Y": 100},
                    "Preview": True, "Enabled": True}
            data["Components"].append(node)
            result = {"node_id": node_id, "inputs": spec.get("inputs", []),
                      "outputs": spec.get("outputs", [])}
        elif name == "wb_add_parameter":
            typ = str(args.get("param_type") or "")
            if not typ:
                raise BridgeError("A parameter type is required.")
            node_id = self._id(data, str(args.get("node_id") or args.get("nickname") or typ))
            node = {"Id": node_id, "Type": typ, "NickName": str(args.get("nickname") or typ),
                    "Category": "Params", "SubCategory": "Input",
                    "Position": args.get("position") or {"X": 0, "Y": 100 * len(data["Parameters"])},
                    "Preview": False, "Enabled": True}
            node.update(args.get("extra") or {})
            data["Parameters"].append(node)
            result = {"node_id": node_id}
        elif name == "wb_connect":
            source, target = str(args.get("source_id", "")), str(args.get("target_id", ""))
            self._node(data, source)
            self._node(data, target)
            wire = {"Source": {"Id": source, "Port": args.get("source_port")},
                    "Target": {"Id": target, "Port": args.get("target_port")}}
            if wire not in data["Connections"]:
                data["Connections"].append(wire)
            result = {"connection_count": len(data["Connections"])}
        elif name == "wb_disconnect":
            before = len(data["Connections"])
            data["Connections"] = [wire for wire in data["Connections"] if not (
                wire["Target"]["Id"] == args.get("target_id") and
                wire["Target"]["Port"] == args.get("target_port"))]
            result = {"removed": before - len(data["Connections"])}
        elif name == "wb_set_group":
            node_ids = args.get("node_ids")
            if not isinstance(node_ids, list) or not node_ids:
                raise BridgeError("A group needs named nodes.")
            for node_id in node_ids:
                self._node(data, node_id)
            data["Groups"].append({"Name": str(args.get("name") or "Group"), "Objects": node_ids})
            result = {"group_count": len(data["Groups"])}
        elif name == "wb_update_node":
            node = self._node(data, str(args.get("node_id") or ""))
            updates = args.get("updates")
            if not isinstance(updates, dict) or "Id" in updates or "Type" in updates:
                raise BridgeError("Choose editable node fields without Id or Type.")
            node.update(updates)
            if "NickName" in updates:
                node["Nickname"] = updates["NickName"]
            result = {"node_id": node["Id"], "updated": sorted(updates)}
        elif name == "wb_remove_node":
            node_id = str(args.get("node_id") or "")
            self._node(data, node_id)
            for key in ("Components", "Parameters"):
                data[key] = [n for n in data[key] if n.get("Id") != node_id]
            data["Connections"] = [w for w in data["Connections"] if node_id not in
                                   (w["Source"]["Id"], w["Target"]["Id"])]
            for group in data["Groups"]:
                group["Objects"] = [n for n in group.get("Objects", []) if n != node_id]
            result = {"removed": node_id}
        else:
            raise BridgeError("Unknown workbench tool.")
        self._save(path, data)
        return result
