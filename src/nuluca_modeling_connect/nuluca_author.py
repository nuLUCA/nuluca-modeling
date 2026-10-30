"""Helpers a member's Blender script can import: ``import nuluca_author``. Runs inside Blender only."""

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np


def arguments():
    """The arguments the run was given: the artifacts folder first, then inputs, then args."""
    return sys.argv[sys.argv.index("--") + 1:]


def artifacts_dir():
    return Path(arguments()[0])


def gn_modifier(obj):
    return next((mod for mod in obj.modifiers if mod.type == "NODES" and mod.node_group), None)


def gn_controls(obj):
    """Named Geometry Nodes inputs on an object, with their current values."""
    modifier = gn_modifier(obj)
    if modifier is None:
        return []
    controls = []
    for item in modifier.node_group.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT" or item.socket_type == "NodeSocketGeometry":
            continue
        entry = {"name": item.name, "type": item.socket_type.removeprefix("NodeSocket")}
        for field in ("min_value", "max_value", "default_value"):
            value = getattr(item, field, None)
            if value is not None:
                entry[field] = _plain(value)
        entry["value"] = _plain(_read(modifier, item.identifier))
        controls.append(entry)
    return controls


def _plain(value):
    """A value JSON can hold. Object, material and collection inputs report their name."""
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, bpy.types.ID):
        return value.name
    try:
        return list(value)
    except TypeError:
        return str(value)


def _inputs(modifier):
    properties = getattr(modifier, "properties", None)
    return getattr(properties, "inputs", None)


def _read(modifier, identifier):
    inputs = _inputs(modifier)
    if inputs is not None and hasattr(inputs, identifier):
        return getattr(getattr(inputs, identifier), "value", None)
    return modifier.get(identifier)


def set_control(obj, name, value):
    """Set one named Geometry Nodes input. Blender 5.2 needs the typed input surface."""
    modifier = gn_modifier(obj)
    if modifier is None:
        raise ValueError(f"{obj.name} has no Geometry Nodes modifier")
    sockets = {item.name: item.identifier for item in modifier.node_group.interface.items_tree
               if item.item_type == "SOCKET" and item.in_out == "INPUT"}
    if name not in sockets:
        raise ValueError(f"Unknown control {name!r}. Available: {sorted(sockets)}")
    inputs = _inputs(modifier)
    if inputs is not None:
        getattr(inputs, sockets[name]).value = value
    else:
        modifier[sockets[name]] = value
    obj.update_tag()


def rendered_objects():
    return [obj for obj in bpy.context.view_layer.objects if not obj.hide_render and obj.visible_get()]


def measure(objects=None):
    """Evaluated counts, bounds and an instance-transform digest for one object, a list or the whole scene.

    ``bounds`` is in Blender units. ``bounds_m`` applies the scene's unit scale.
    """
    wanted = None if objects is None else set(objects) if isinstance(objects, (list, tuple, set)) else {objects}
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    low, high = np.full(3, math.inf), np.full(3, -math.inf)
    instances = vertices = polygons = triangles = 0
    points, triangle_counts = {}, {}
    digest = hashlib.sha256()
    for item in graph.object_instances:
        owner = item.parent.original if item.is_instance and item.parent else item.object.original
        if wanted is not None and owner not in wanted:
            continue
        mesh = item.object.data
        if not isinstance(mesh, bpy.types.Mesh) or not mesh.vertices:
            continue
        instances += item.is_instance
        vertices += len(mesh.vertices)
        polygons += len(mesh.polygons)
        key = mesh.as_pointer()
        if key not in points:
            local = np.empty(len(mesh.vertices) * 3)
            mesh.vertices.foreach_get("co", local)
            points[key] = local.reshape(-1, 3)
            mesh.calc_loop_triangles()
            triangle_counts[key] = len(mesh.loop_triangles)
        triangles += triangle_counts[key]
        matrix = np.array(item.matrix_world)
        digest.update(json.dumps([round(float(x), 8) for x in matrix.flat]).encode())
        world = points[key] @ matrix[:3, :3].T + matrix[:3, 3]
        low, high = np.minimum(low, world.min(0)), np.maximum(high, world.max(0))
    finite = bool(vertices) and bool(np.isfinite(low).all() and np.isfinite(high).all())
    scale = bpy.context.scene.unit_settings.scale_length
    return {"instance_count": int(instances), "expanded_vertices": vertices, "expanded_polygons": polygons,
            "expanded_triangles": triangles, "bounds": [low.tolist(), high.tolist()] if finite else None,
            "bounds_m": [(low * scale).tolist(), (high * scale).tolist()] if finite else None,
            "transform_sha256": digest.hexdigest()}


def sliver_guard(vertices, triangles, ratio=50.0):
    """Check a mesh before an exact crossing or overlap test.

    Very long triangles make broad-phase searches quadratic, and that can exhaust memory.
    Returns (ok, largest extent, median extent).
    """
    corners = np.asarray(vertices, float)[np.asarray(triangles)]
    extent = (corners.max(1) - corners.min(1)).max(1)
    median = float(np.median(extent))
    largest = float(extent.max())
    return largest <= ratio * max(median, 1e-12), largest, median
