"""Fixed steps that run inside a background Blender. Never imported by the wheel.

Arguments after ``--``: artifacts folder, step name, request JSON path. The source
.blend is opened with scripts off and never saved over. Every step writes
``result.json`` into the artifacts folder.
"""

import hashlib
import json
import math
import platform
import re
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

import nuluca_author as author

_MESH_HEALTH_VERTEX_LIMIT = 200_000
_OBJECT_LIMIT = 200


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_native(path):
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)


def capabilities(_request, _out):
    return {"blender": bpy.app.version_string, "build_hash": bpy.app.build_hash.decode(),
            "python": sys.version, "platform": platform.platform(),
            "gltf_export_properties": sorted(bpy.ops.export_scene.gltf.get_rna_type().properties.keys()),
            "usd_export_properties": sorted(bpy.ops.wm.usd_export.get_rna_type().properties.keys()),
            "geometry_node_types": sorted(name for name in dir(bpy.types) if name.startswith("GeometryNode")),
            "external_addons": sorted(bpy.context.preferences.addons.keys())}


def _mesh_health(obj):
    mesh = obj.data
    if len(mesh.vertices) > _MESH_HEALTH_VERTEX_LIMIT:
        return {"skipped": f"over {_MESH_HEALTH_VERTEX_LIMIT} vertices"}
    shape = bmesh.new()
    shape.from_mesh(mesh)
    try:
        loose = sum(not vertex.link_edges for vertex in shape.verts)
        open_edges = sum(edge.is_boundary for edge in shape.edges)
        nonmanifold = sum(not edge.is_manifold and not edge.is_boundary for edge in shape.edges)
        zero_area = sum(face.calc_area() < 1e-12 for face in shape.faces)
    finally:
        shape.free()
    return {"loose_vertices": loose, "open_edges": open_edges, "nonmanifold_edges": nonmanifold,
            "zero_area_faces": zero_area}


def inspect(request, _out):
    native = Path(request["file"])
    started = time.perf_counter()
    open_native(native)
    opened = time.perf_counter() - started
    scene = bpy.context.scene
    objects = []
    for obj in list(scene.objects)[:_OBJECT_LIMIT]:
        record = {"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else None,
                  "collections": [item.name for item in obj.users_collection],
                  "hidden_in_render": obj.hide_render, "hidden_in_viewport": obj.hide_viewport,
                  "modifiers": [{"name": mod.name, "type": mod.type} for mod in obj.modifiers],
                  "materials": [slot.material.name for slot in obj.material_slots if slot.material]}
        if obj.type == "MESH":
            record["evaluated"] = author.measure(obj)
            record["mesh"] = {"vertices": len(obj.data.vertices), "polygons": len(obj.data.polygons),
                              **_mesh_health(obj)}
            record["controls"] = author.gn_controls(obj)
        objects.append(record)
    return {"source_sha256": sha(native), "open_seconds": opened,
            "blender": bpy.app.version_string, "units": {"system": scene.unit_settings.system,
                                                         "scale_length": scene.unit_settings.scale_length},
            "frame": [scene.frame_start, scene.frame_current, scene.frame_end],
            "scene": author.measure(), "object_count": len(scene.objects), "objects": objects,
            "objects_truncated": len(scene.objects) > _OBJECT_LIMIT,
            "materials": [{"name": item.name, "users": item.users, "fake_user": item.use_fake_user}
                          for item in bpy.data.materials],
            "cameras": [item.name for item in scene.objects if item.type == "CAMERA"],
            "active_camera": scene.camera.name if scene.camera else None,
            "checked_seconds": time.perf_counter() - started}


def _same(before, after):
    return [key for key in ("instance_count", "expanded_vertices", "expanded_polygons", "expanded_triangles",
                            "bounds_m", "transform_sha256") if before[key] != after[key]]


def vary(request, out):
    """Reopen the same source for every case, change named controls, save, reopen, compare."""
    native, name = Path(request["file"]), request["object"]
    results = {"source_sha256": sha(native), "object": name, "cases": []}
    for case, values in request["cases"].items():
        record = {"name": case, "controls": values}
        try:
            started = time.perf_counter()
            open_native(native)
            record["open_seconds"] = time.perf_counter() - started
            obj = bpy.data.objects[name]
            started = time.perf_counter()
            for control, value in values.items():
                author.set_control(obj, control, value)
            bpy.context.view_layer.update()
            record["control_update_seconds"] = time.perf_counter() - started
            before = author.measure(obj)
            path = out / f"{case}.blend"
            started = time.perf_counter()
            bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)
            record["save_seconds"] = time.perf_counter() - started
            open_native(path)
            after = author.measure(bpy.data.objects[name])
            record.update({"file": path.name, "native_sha256": sha(path), "native_bytes": path.stat().st_size,
                           "before": before, "after_reopen": after, "changed_on_reopen": _same(before, after)})
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        results["cases"].append(record)
    results["all_cases_stable"] = all("error" not in item and not item["changed_on_reopen"]
                                      for item in results["cases"])
    return results


def _select(target):
    bpy.ops.object.select_all(action="DESELECT")
    chosen = [bpy.data.objects[target]] if target else [
        obj for obj in bpy.context.view_layer.objects
        if not obj.hide_render and obj.visible_get() and obj.type in {"MESH", "EMPTY", "CURVE", "CURVES", "POINTCLOUD"}]
    for obj in chosen:
        obj.select_set(True)
    if chosen:
        bpy.context.view_layer.objects.active = chosen[0]
    return chosen


def _gltf(path, record):
    wanted = {"filepath": str(path), "export_format": "GLB", "use_selection": True, "export_apply": True,
              "export_yup": True, "export_animations": False, "export_extras": True, "export_materials": "EXPORT",
              "export_gpu_instances": False}
    available = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
    record["settings_this_blender_lacks"] = sorted(set(wanted) - available)
    bpy.ops.export_scene.gltf(**{key: value for key, value in wanted.items() if key in available})
    record["keeps"] = ["evaluated mesh", "simple PBR material", "metres", "object extras"]
    record["loses"] = ["Geometry Nodes and controls", "native instancing", "time behaviour", "cameras and lights"]


def _usd(path, record):
    bpy.ops.wm.usd_export(filepath=str(path), selected_objects_only=True, export_materials=True)
    record["keeps"] = ["evaluated geometry", "transforms", "materials"]
    record["loses"] = ["Geometry Nodes and controls"]


def _stl(path, record):
    bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True, apply_modifiers=True,
                          use_scene_unit=True)
    record["keeps"] = ["evaluated triangles at scene scale"]
    record["loses"] = ["materials", "object separation", "controls"]


def _obj(path, record):
    bpy.ops.wm.obj_export(filepath=str(path), export_selected_objects=True, apply_modifiers=True,
                          export_materials=False)
    record["keeps"] = ["evaluated mesh", "object names"]
    record["loses"] = ["materials", "controls"]


_EXPORTERS = {"glb": _gltf, "usd": _usd, "stl": _stl, "obj": _obj}


def _readback(path):
    """Import a GLB into the emptied scene. The source file on disk is untouched."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    started = time.perf_counter()
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = time.perf_counter() - started
    state = author.measure()
    return {"import_seconds": imported, "vertices": state["expanded_vertices"],
            "triangles": state["expanded_triangles"], "bounds_m": state["bounds_m"]}


def export(request, out):
    native, kind = Path(request["file"]), request["format"]
    if kind not in _EXPORTERS:
        raise ValueError("Unknown export format")
    open_native(native)
    target = request.get("object")
    chosen = _select(target)
    if not chosen:
        raise ValueError("Nothing to export: no rendered geometry in this file")
    realized = []
    for obj in chosen:
        if any(item["name"] == "Realize for export" for item in author.gn_controls(obj)):
            author.set_control(obj, "Realize for export", True)
            realized.append(obj.name)
    state = author.measure(chosen)
    path = out / f"{native.stem}.{kind}"
    record = {"format": kind, "source_sha256": sha(native), "realized_for_export": realized,
              "exported_objects": [obj.name for obj in chosen], "evaluated": state}
    started = time.perf_counter()
    _EXPORTERS[kind](path, record)
    record.update({"export_seconds": time.perf_counter() - started, "file": path.name,
                   "sha256": sha(path), "bytes": path.stat().st_size})
    if kind == "glb":
        record["readback"] = _readback(path)
        if state["bounds_m"] and record["readback"]["bounds_m"]:
            record["bounds_delta_m"] = max(abs(a - b) for row, other in zip(state["bounds_m"],
                                                                           record["readback"]["bounds_m"])
                                           for a, b in zip(row, other))
    return record


_VIEWS = {"iso": (1, -1, 0.8), "front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
          "top": (0, 0, 1), "bottom": (0, 0, -1)}


def _frame(bounds, aspect):
    low, high = Vector(bounds[0]), Vector(bounds[1])
    centre = (low + high) / 2
    radius = max((high - low).length / 2, 1e-3)
    return centre, radius, radius * 2.2 * max(1.0, aspect)


def preview(request, out):
    """Quick shaded views of the exact file. These are review views for the agent, never renderings."""
    native = Path(request["file"])
    open_native(native)
    target = bpy.data.objects[request["object"]] if request.get("object") else None
    for control, value in (request.get("controls") or {}).items():
        author.set_control(target, control, value)
    state = author.measure(target or author.rendered_objects())
    if not state["bounds_m"]:
        raise ValueError("Nothing to preview: no evaluated mesh geometry")
    scene = bpy.context.scene
    width, height = request["width"], request["height"]
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    shading = scene.display.shading
    shading.light, shading.color_type = "STUDIO", "MATERIAL"
    shading.show_shadows, shading.show_cavity = True, True
    camera_data = bpy.data.cameras.new("nuluca-preview")
    camera = bpy.data.objects.new("nuluca-preview", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.clip_start, camera_data.clip_end = 1e-4, 1e6
    centre, radius, scale = _frame(state["bounds_m"], width / height)
    camera_data.ortho_scale = scale
    images = []
    for name in request["views"]:
        direction = Vector(_VIEWS[name]).normalized()
        camera.location = centre + direction * radius * 4
        camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        path = out / f"{name}.png"
        scene.render.filepath = str(path)
        started = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        images.append({"view": name, "file": path.name, "render_seconds": time.perf_counter() - started})
    return {"source_sha256": sha(native), "engine": scene.render.engine, "label": "exact review view",
            "evaluated": state, "ortho_scale_m": scale, "images": images}


_STEPS = {"capabilities": capabilities, "inspect": inspect, "vary": vary, "export": export, "preview": preview}


def main():
    arguments = author.arguments()
    out, step, request_path = Path(arguments[0]), arguments[1], Path(arguments[2])
    if not re.fullmatch(r"[a-z]+", step) or step not in _STEPS:
        raise ValueError("Unknown Blender step")
    started = time.perf_counter()
    try:
        result = _STEPS[step](json.loads(request_path.read_text()), out)
        result["step_seconds"] = time.perf_counter() - started
    except Exception as exc:
        (out / "result.json").write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        raise
    (out / "result.json").write_text(json.dumps(result, indent=2, default=_plain, allow_nan=False))


def _plain(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


main()
