"""Draft member-safe outline of a Blender method. Not shipped in the wheel.

The corpus stores a method as ``blender-python-bundle/v1``: full source files,
assets, controls, lineage and execution notes. This function is the shape a future
member-facing operation could return. It reads the bundle on the server and copies
only named fields, so a new bundle field stays private until someone adds it here.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

FORMAT = "blender-method-outline/v1"
_NODE_TYPE = re.compile(r"^(?!.*Tree$)(GeometryNode|ShaderNode|FunctionNode|NodeGroup)[A-Za-z0-9]*$")
_MODIFIER_TYPE = re.compile(r"^[A-Z][A-Z_]{2,}$")
_CONTROL_FIELDS = ("name", "unit", "kind", "type", "min", "max", "default")
_LIMITS = {"title": 160, "summary": 600, "control": 80}
_CODE_SHAPE = re.compile(r"\bdef \w+\(|\bimport \w+|\bbpy\.|\blambda\b|=\s*\[|\bfor \w+ in\b")


def _surface(text: str) -> tuple[set[str], set[str], set[str], dict[str, int]]:
    nodes: set[str] = set()
    modifiers: set[str] = set()
    operators: set[str] = set()
    tree = ast.parse(text)
    counts = {"functions": 0, "classes": 0}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts["functions"] += 1
        elif isinstance(node, ast.ClassDef):
            counts["classes"] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _NODE_TYPE.fullmatch(node.value):
            nodes.add(node.value)
        elif isinstance(node, ast.Call):
            target = node.func
            if (isinstance(target, ast.Attribute) and target.attr == "new" and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "modifiers" and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
                    and _MODIFIER_TYPE.fullmatch(node.args[1].value)):
                modifiers.add(node.args[1].value)
            chain = []
            while isinstance(target, ast.Attribute):
                chain.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name) and target.id == "bpy" and len(chain) >= 3 and chain[-1] == "ops":
                operators.add(".".join(reversed(chain[:-1])))
    return nodes, modifiers, operators, counts


def _text(value: Any, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if len(text) > limit or _CODE_SHAPE.search(text):
        return ""
    return text


def member_outline(script: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """Describe what a method does and how to drive it, with no source, literals or names from the code."""
    nodes: set[str] = set()
    modifiers: set[str] = set()
    operators: set[str] = set()
    functions = classes = lines = 0
    for item in body["files"]:
        if item["name"].endswith(".py"):
            found = _surface(item["text"])
            nodes |= found[0]
            modifiers |= found[1]
            operators |= found[2]
            functions += found[3]["functions"]
            classes += found[3]["classes"]
        lines += item["text"].count("\n") + 1
    controls = [{field: (_text(control[field], _LIMITS["control"]) if isinstance(control[field], str)
                         else control[field])
                 for field in _CONTROL_FIELDS if field in control and not isinstance(control[field], (dict, list))}
                for control in body["controls"] if isinstance(control, Mapping)]
    outline = {
        "format": FORMAT,
        "id": script["id"],
        "version": script.get("corpus_version"),
        "curation": script.get("curation_status"),
        "title": _text(script.get("title") or script.get("name"), _LIMITS["title"]),
        "summary": _text(script.get("description"), _LIMITS["summary"]),
        "runtime": {"blender": body["runtime"]["version"]},
        "controls": controls,
        "structure": {"source_files": len(body["files"]), "source_lines": lines, "functions": functions,
                      "classes": classes, "assets": len(body["assets"])},
        "blender_surface": {"node_types": sorted(nodes), "modifier_types": sorted(modifiers),
                            "operators": sorted(operators)},
    }
    assert_safe(outline)
    return outline


def assert_safe(outline: Mapping[str, Any]) -> None:
    """A last check on the way out. Refuses anything that reads as source."""
    forbidden = {"files", "text", "source", "body", "entrypoint", "assets_url", "lineage", "execution", "agent_notes"}

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if forbidden & set(value):
                raise ValueError("private field in outline")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and (len(value) > _LIMITS["summary"] or _CODE_SHAPE.search(value)):
            raise ValueError("source-shaped text in outline")

    walk(outline)
