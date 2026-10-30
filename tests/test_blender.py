"""Local Blender tools: discovery, guards, confinement and, when available, a real Blender."""

import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

import nuluca_modeling_connect
from nuluca_modeling_connect import blender_discovery as discovery
from nuluca_modeling_connect import blender_memory as memory
from nuluca_modeling_connect.blender import BlenderError, BlenderTools, blender_tools
from nuluca_modeling_connect.server import make_server

PACKAGE = Path(nuluca_modeling_connect.__file__).parent
REAL_BLENDER = os.environ.get("NULUCA_TEST_BLENDER")
posix_only = pytest.mark.skipif(os.name != "posix", reason="the fake Blender is a shell script")
needs_blender = pytest.mark.skipif(not REAL_BLENDER, reason="set NULUCA_TEST_BLENDER to a Blender path")

FAKE = """#!/bin/sh
if [ "$1" = "--version" ]; then
  printf 'Blender 5.2.2 LTS\\n\\tbuild hash: d13f752e3b9c\\n'
  exit 0
fi
env > "$PWD/child-env.txt"
if [ -n "$(cat "$PWD/fake-sleep" 2>/dev/null)" ]; then sleep "$(cat "$PWD/fake-sleep")"; fi
exit 0
"""


def fake_blender(tmp_path: Path, *, sleep: int = 0, numbers: str = "5.2.2") -> Path:
    path = tmp_path / "bin" / "blender"
    path.parent.mkdir(exist_ok=True)
    body = FAKE.replace("5.2.2", numbers)
    if sleep:
        body = body.replace('sleep "$(cat "$PWD/fake-sleep")"', f"sleep {sleep}").replace(
            'if [ -n "$(cat "$PWD/fake-sleep" 2>/dev/null)" ]; then', "if true; then")
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def tools(tmp_path: Path, binary: Path, **kwargs) -> BlenderTools:
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "NULUCA_BLENDER_PATH": str(binary),
           "NULUCA_AGENT_CONNECT_KEY": "ae_secret_for_test", "OPENAI_API_KEY": "sk-secret-for-test",
           **kwargs.pop("env", {})}
    return BlenderTools(root=tmp_path / "work", env=env, read_memory=lambda: 10.0, **kwargs)


def write_script(work: BlenderTools, source: str = "print('hello')\n") -> str:
    work.root.mkdir(parents=True, exist_ok=True)
    (work.root / "model.py").write_text(source)
    return "model.py"


# Discovery ----------------------------------------------------------------

@pytest.mark.parametrize("text,numbers,build", [
    ("Blender 5.2.2 LTS\n\tbuild hash: d13f752e3b9c\n", (5, 2, 2), "d13f752e3b9c"),
    ("Blender 4.2.1\n", (4, 2, 1), None),
    ("Blender 5.3\n\tbuild hash: abc123\n", (5, 3), "abc123"),
])
def test_version_output_is_parsed(text, numbers, build):
    parsed = discovery.parse_version(text)
    assert parsed[1:] == (numbers, build)


def test_unknown_version_output_is_refused():
    assert discovery.parse_version("Welcome to something else") is None


@pytest.mark.parametrize("numbers,support", [((5, 2, 2), "tested"), ((5, 3, 0), "untested"), ((4, 2), "untested"),
                                             ((4, 1, 1), "too_old"), ((3, 6, 5), "too_old")])
def test_version_support(numbers, support):
    assert discovery.support_for(numbers) == support


def test_mac_app_bundle_resolves_to_its_binary(tmp_path):
    app = tmp_path / "Blender.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "Blender").write_text("")
    assert discovery.executable(tmp_path / "Blender.app", "Darwin") == app / "Blender"
    assert discovery.executable(tmp_path / "Missing.app", "Darwin") is None


def test_mac_candidates_cover_versioned_apps_steam_and_spotlight(tmp_path):
    (tmp_path / "Applications" / "Blender 5.2.app").mkdir(parents=True)
    found = discovery.candidates("Darwin", {}, tmp_path, which=lambda _: None,
                                 spotlight=lambda: ["/Volumes/Tools/Blender.app"], registry=lambda: [])
    names = {str(path): source for path, source in found}
    assert names[str(tmp_path / "Applications" / "Blender 5.2.app")] == "applications"
    assert names["/Volumes/Tools/Blender.app"] == "spotlight"
    assert any(source == "steam" for source in names.values())


def test_windows_candidates_cover_program_files_steam_registry_and_store(tmp_path):
    (tmp_path / "pf" / "Blender Foundation" / "Blender 5.2").mkdir(parents=True)
    env = {"ProgramFiles": str(tmp_path / "pf"), "LOCALAPPDATA": str(tmp_path / "local")}
    launcher = tmp_path / "reg" / "blender-launcher.exe"
    found = discovery.candidates("Windows", env, tmp_path, which=lambda _: None, spotlight=lambda: [],
                                 registry=lambda: [f'"{launcher}" "%1"'])
    paths = {path: source for path, source in found}
    assert paths[tmp_path / "pf" / "Blender Foundation" / "Blender 5.2" / "blender.exe"] == "program files"
    assert paths[tmp_path / "reg" / "blender.exe"] == "file association"
    assert paths[tmp_path / "local" / "Microsoft/WindowsApps/blender.exe"] == "microsoft store"
    assert any(source == "steam" for source in paths.values())


def _install(version, support="tested"):
    return discovery.Install("/b", "/r", "test", f"Blender {version}", tuple(map(int, version.split("."))), None, support)


def test_tested_version_beats_newer_untested_and_too_old_is_never_chosen(tmp_path):
    binaries = {}
    for name, install in (("a", _install("5.2.2")), ("b", _install("5.4.0", "untested")),
                          ("c", _install("3.6.0", "too_old"))):
        path = tmp_path / name
        path.write_text("")
        binaries[path] = install
    found = discovery.discover({}, {}, system="Linux", home=tmp_path,
                               search=lambda *_: [(path, "test") for path in binaries],
                               read=lambda path, *_: binaries[path])
    assert found["selected"].version == "Blender 5.2.2"
    assert len(found["installs"]) == 3


def test_a_wrong_named_path_is_reported_and_nothing_is_substituted(tmp_path):
    found = discovery.discover({"NULUCA_BLENDER_PATH": str(tmp_path / "nope")}, {}, system="Linux", home=tmp_path,
                               search=lambda *_: pytest.fail("must not search"))
    assert found["selected"] is None
    assert found["rejected"][0]["source"] == "NULUCA_BLENDER_PATH"


# Memory -------------------------------------------------------------------

def test_memory_ceiling_shrinks_on_a_small_machine():
    assert memory.default_limit_gb(8) == 4.8
    assert memory.default_limit_gb(64) == 12.0


def test_commit_gate_waits_then_halts():
    values = iter([90.0, 90.0, 90.0, 90.0])
    result = memory.wait_for_commit(85, 0, read=lambda: next(values), sleep=lambda _: None)
    assert result["ok"] is False and result["first"] == 90.0


def test_commit_gate_passes_when_memory_recovers_or_is_unreadable():
    values = iter([90.0, 50.0])
    assert memory.wait_for_commit(85, 60, read=lambda: next(values), sleep=lambda _: None)["ok"]
    assert memory.wait_for_commit(85, 60, read=lambda: None)["available"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="the job object is active on Windows")
def test_memory_job_says_it_is_inert_off_windows():
    job = memory.ProcessMemoryJob(4)
    assert job.creationflags() == 0 and job.status == "unavailable: not Windows"


# Confinement --------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../outside.py", "/etc/outside.py", "a/../../outside.py"])
async def test_paths_stay_in_the_work_folder(tmp_path, name):
    work = BlenderTools(root=tmp_path / "work", env={"PATH": os.environ["PATH"]})
    with pytest.raises(BlenderError, match="inside"):
        await work.call("blender_write", {"file": name, "source": "x = 1\n"})
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.asyncio
async def test_a_symlink_cannot_lead_out_of_the_work_folder(tmp_path):
    work = BlenderTools(root=tmp_path / "work", env={"PATH": os.environ["PATH"]})
    work.root.mkdir()
    (tmp_path / "secret").mkdir()
    (work.root / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(BlenderError, match="inside"):
        await work.call("blender_write", {"file": "link/model.py", "source": "x = 1\n"})


@pytest.mark.asyncio
@posix_only
async def test_inputs_must_be_in_the_work_folder(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path))
    script = write_script(work)
    outside = tmp_path / "private.blend"
    outside.write_text("x")
    with pytest.raises(BlenderError, match="Every input"):
        await work.call("blender_run", {"script": script, "inputs": [str(outside)]})


@pytest.mark.asyncio
@posix_only
async def test_child_environment_carries_no_keys(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path))
    report = await work.call("blender_run", {"script": write_script(work)})
    assert report["status"] == "process_completed"
    child_env = (work.root / "blender-runs" / report["run"] / "child-env.txt").read_text()
    assert "NULUCA_AGENT_CONNECT_KEY" not in child_env and "ae_secret_for_test" not in child_env
    assert "OPENAI_API_KEY" not in child_env and "sk-secret" not in child_env
    assert "BLENDER_USER_CONFIG=" in child_env and "PYTHONDONTWRITEBYTECODE=1" in child_env


@pytest.mark.asyncio
@posix_only
async def test_command_uses_factory_startup_and_autoexec_off(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path))
    report = await work.call("blender_run", {"script": write_script(work), "args": ["two"]})
    receipt = json.loads((work.root / report["receipt"]).read_text())
    command = receipt["command"]
    for flag in ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode"):
        assert flag in command
    assert command.index("--python") < command.index("--")
    assert command[-1] == "two"
    assert receipt["source_sha256"] and receipt["guard_sha256"]


# Modes --------------------------------------------------------------------

def test_tool_modes():
    names = lambda mode: {item["name"] for item in blender_tools({"NULUCA_MODELING_BLENDER": mode})}
    assert {"blender_run", "blender_write", "blender_inspect"} <= names("on")
    assert names("fixed") == names("on") - {"blender_run", "blender_write"}
    assert names("off") == set()
    assert names("nonsense") == set()


@pytest.mark.asyncio
async def test_fixed_mode_refuses_python(tmp_path):
    work = BlenderTools(root=tmp_path / "work", env={"NULUCA_MODELING_BLENDER": "fixed"})
    with pytest.raises(BlenderError, match="turned off"):
        await work.call("blender_run", {"script": "x.py"})


def test_only_the_run_tool_is_marked_as_code_execution():
    marked = [item["name"] for item in blender_tools({}) if item.get("annotations", {}).get("destructiveHint")]
    assert marked == ["blender_run"]


# Stopping -----------------------------------------------------------------

@pytest.mark.asyncio
@posix_only
async def test_a_long_run_returns_early_and_can_be_cancelled(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path, sleep=30))
    report = await work.call("blender_run", {"script": write_script(work), "wait_seconds": 0})
    assert report["status"] == "running"
    with pytest.raises(BlenderError, match="still going"):
        await work.call("blender_run", {"script": "model.py", "wait_seconds": 0})
    stopped = await work.call("blender_cancel", {"run": report["run"]})
    assert stopped["status"] == "cancelled_artifacts_preserved"
    assert (await work.call("blender_status", {}))["active_run"] is None


@pytest.mark.asyncio
@posix_only
async def test_timeout_stops_only_the_child(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path, sleep=30))
    report = await work.call("blender_run", {"script": write_script(work), "timeout_seconds": 0.3, "wait_seconds": 10})
    assert report["status"] == "timed_out_artifacts_preserved"
    assert json.loads((work.root / report["receipt"]).read_text())["exit_code"] is not None


@pytest.mark.asyncio
@posix_only
async def test_memory_ceiling_stops_the_child(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path, sleep=30))
    report = await work.call("blender_run", {"script": write_script(work), "memory_limit_gb": 0.000001,
                                             "wait_seconds": 10})
    assert report["status"] == "stopped_memory_limit_artifacts_preserved"


@pytest.mark.asyncio
@posix_only
async def test_low_machine_memory_stops_the_child(tmp_path):
    readings = iter([10.0] + [99.0] * 1000)
    work = tools(tmp_path, fake_blender(tmp_path, sleep=30))
    work.read_memory = lambda: next(readings)
    report = await work.call("blender_run", {"script": write_script(work), "wait_seconds": 10})
    assert report["status"] == "stopped_low_memory_artifacts_preserved"


@pytest.mark.asyncio
@posix_only
async def test_commit_gate_halts_without_launching(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "wait_for_commit", lambda **_: {"ok": False, "threshold": 85.0})
    work = tools(tmp_path, fake_blender(tmp_path))
    report = await work.call("blender_run", {"script": write_script(work)})
    assert report["status"] == "halted_commit_gate"
    assert not (work.root / "blender-runs" / report["run"] / "child-env.txt").exists()


@pytest.mark.asyncio
@posix_only
async def test_close_leaves_no_child_running(tmp_path):
    work = tools(tmp_path, fake_blender(tmp_path, sleep=30))
    report = await work.call("blender_run", {"script": write_script(work), "wait_seconds": 0})
    process = work._runs[report["run"]].process
    await asyncio.to_thread(work.close)
    assert process.poll() is not None


def test_no_code_path_reaches_a_member_application():
    banned = re.compile(r"pkill|killall|taskkill|osascript|psutil|Stop-Process|os\.kill\((?!\w+, 0\))|SIGKILL|tell application")
    for path in PACKAGE.glob("*"):
        if path.suffix in {".py", ".md"} and path.name != "blender_guide.md":
            assert not banned.search(path.read_text()), path
    host = (PACKAGE / "blender.py").read_text()
    assert re.findall(r"(\w+)\.(?:terminate|kill)\(\)", host) == ["process", "process"]
    assert "def _stop(process: subprocess.Popen)" in host


def test_blender_only_modules_are_not_imported_by_the_wheel():
    in_blender = {"blender_guard", "blender_steps", "nuluca_author"}
    for path in PACKAGE.glob("*.py"):
        if path.stem in in_blender:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not in_blender & {node.module or "", *(item.name for item in node.names)}, path
            elif isinstance(node, ast.Import):
                assert not in_blender & {item.name for item in node.names}, path


# Server -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_lists_blender_tools_and_returns_preview_images(tmp_path):
    class Fake:
        async def call(self, name, arguments):
            return {"status": "process_completed", "_images": ["aW1hZ2U="]}

    server = make_server(None, blender=Fake())
    listed = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    tool_names = {item.name for item in listed.root.tools}
    assert {"blender_status", "blender_preview", "blender_run", "canvas_status"} <= tool_names
    reply = await server.request_handlers[CallToolRequest](CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name="blender_preview", arguments={"file": "a.blend"})))
    kinds = [item.type for item in reply.root.content]
    assert kinds == ["text", "image"]
    assert "_images" not in reply.root.content[0].text


@pytest.mark.asyncio
async def test_guide_is_packaged_and_keeps_private_vocabulary_out(tmp_path):
    work = BlenderTools(root=tmp_path / "work", env={})
    guide = (await work.call("blender_guide", {}))["guide"]
    assert "Never quit, kill or relaunch" in guide
    for private in ("S19", "S20", "rubric", "reviewer", "critic", "harness"):
        assert private.lower() not in guide.lower()


# Method outlines ----------------------------------------------------------

def _outline_module():
    path = Path(__file__).resolve().parents[1] / "design" / "blender-method-outline" / "outline.py"
    spec = importlib.util.spec_from_file_location("blender_method_outline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = '''"""SECRET_DOCSTRING explains the trick."""
import bpy

SECRET_RATIO = 0.6180339

def secret_phyllotaxis_step(count):
    # SECRET_COMMENT
    bpy.ops.mesh.primitive_cube_add(size=SECRET_RATIO)
    obj = bpy.context.object
    obj.modifiers.new("Screen", "NODES")
    tree = bpy.data.node_groups.new("SECRET_GROUP", "GeometryNodeTree")
    tree.nodes.new("GeometryNodeInstanceOnPoints")
    return obj
'''


def test_outline_carries_no_source_and_keeps_controls():
    module = _outline_module()
    body = {"format": "blender-python-bundle/v1", "runtime": {"version": "5.2.2"}, "entrypoint": "author.py",
            "files": [{"name": "author.py", "text": SOURCE, "sha256": "0" * 64}],
            "assets": [{"name": "m.blend", "sha256": "0" * 64, "bytes": 1, "role": "native"}],
            "controls": [{"name": "Count", "unit": "items", "min": 2, "max": 64, "default": 8, "note": "SECRET_NOTE"}],
            "lineage": ["SECRET_LINEAGE"], "execution": {"note": "SECRET_EXECUTION"}}
    outline = module.member_outline({"id": "m1", "title": "Screen field", "description": "A shading screen."}, body)
    text = json.dumps(outline)
    assert "SECRET" not in text and "secret" not in text and "0.618" not in text
    assert outline["controls"] == [{"name": "Count", "unit": "items", "min": 2, "max": 64, "default": 8}]
    assert outline["blender_surface"] == {"node_types": ["GeometryNodeInstanceOnPoints"], "modifier_types": ["NODES"],
                                          "operators": ["mesh.primitive_cube_add"]}
    assert outline["structure"]["functions"] == 1


def test_outline_drops_code_shaped_summaries_and_refuses_private_fields():
    module = _outline_module()
    body = {"runtime": {"version": "5.2.2"}, "files": [{"name": "a.py", "text": "x = 1\n"}], "assets": [],
            "controls": []}
    outline = module.member_outline({"id": "m", "title": "t", "description": "def trick(): return bpy.data"}, body)
    assert outline["summary"] == ""
    with pytest.raises(ValueError):
        module.assert_safe({"nested": {"files": []}})
    with pytest.raises(ValueError):
        module.assert_safe({"summary": "import bpy"})


# Real Blender -------------------------------------------------------------

MODEL = '''
import bpy
import nuluca_author as author

out = author.artifacts_dir()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_plane_add(size=1)
host = bpy.context.object
host.name = "Field"
bpy.ops.mesh.primitive_cube_add(size=0.05)
unit = bpy.context.object
unit.name = "Unit"
unit.hide_render = True
tree = bpy.data.node_groups.new("Screen", "GeometryNodeTree")
tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
count = tree.interface.new_socket("Count", in_out="INPUT", socket_type="NodeSocketInt")
count.default_value, count.min_value, count.max_value = 8, 2, 64
tree.interface.new_socket("Realize for export", in_out="INPUT", socket_type="NodeSocketBool")
group_in, group_out = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
grid = tree.nodes.new("GeometryNodeMeshGrid")
grid.inputs["Size X"].default_value = grid.inputs["Size Y"].default_value = 1
points = tree.nodes.new("GeometryNodeInstanceOnPoints")
info = tree.nodes.new("GeometryNodeObjectInfo")
info.inputs[0].default_value = unit
realize = tree.nodes.new("GeometryNodeRealizeInstances")
switch = tree.nodes.new("GeometryNodeSwitch")
switch.input_type = "GEOMETRY"
links = tree.links
links.new(group_in.outputs["Count"], grid.inputs["Vertices X"])
links.new(group_in.outputs["Count"], grid.inputs["Vertices Y"])
links.new(grid.outputs[0], points.inputs["Points"])
links.new(info.outputs["Geometry"], points.inputs["Instance"])
links.new(points.outputs[0], realize.inputs[0])
links.new(group_in.outputs["Realize for export"], switch.inputs["Switch"])
links.new(points.outputs[0], switch.inputs["False"])
links.new(realize.outputs[0], switch.inputs["True"])
links.new(switch.outputs[0], group_out.inputs[0])
host.modifiers.new("Screen", "NODES").node_group = tree
bpy.ops.wm.save_as_mainfile(filepath=str(out / "screen.blend"))
'''

HOSTILE = '''
import json
import os
import socket
import subprocess
import nuluca_author as author

home = os.environ["NULUCA_BLENDER_MEMBER_HOME"]
secret = os.path.join(home, "secret.txt")
outcome = {}

def attempt(name, action):
    try:
        action()
        outcome[name] = "allowed"
    except PermissionError:
        outcome[name] = "refused"
    except OSError as exc:
        outcome[name] = type(exc).__name__

attempt("read", lambda: open(secret).read())
attempt("list", lambda: os.listdir(home))
attempt("write", lambda: open(os.path.join(home, "planted.txt"), "w").write("x"))
attempt("delete", lambda: os.remove(secret))
attempt("network", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1))
attempt("process", lambda: subprocess.run(["/bin/echo", "x"]))
attempt("system", lambda: os.system("echo x"))
attempt("work", lambda: (author.artifacts_dir() / "inside.txt").write_text("x"))
(author.artifacts_dir() / "outcome.json").write_text(json.dumps(outcome))
'''

SPIN = '''
import time
while True:
    time.sleep(0.1)
'''


@needs_blender
@pytest.mark.asyncio
async def test_real_blender_loop(tmp_path):
    work = BlenderTools(root=tmp_path / "work", env={**os.environ, "NULUCA_BLENDER_PATH": REAL_BLENDER,
                                                     "NULUCA_AGENT_CONNECT_KEY": "ae_secret_for_test"})
    status = await work.call("blender_status", {})
    assert status["selected"]["support"] in {"tested", "untested"}
    await work.call("blender_write", {"file": "screen.py", "source": MODEL})
    built = await work.call("blender_run", {"script": "screen.py"})
    assert built["status"] == "process_completed", built.get("log_tail")
    assert built["peak_rss_bytes"] > 50_000_000
    blend = next(item["path"] for item in built["artifacts"] if item["path"].endswith(".blend"))
    source_hash = next(item["sha256"] for item in built["artifacts"] if item["path"].endswith(".blend"))

    seen = await work.call("blender_inspect", {"file": blend})
    field = next(item for item in seen["result"]["objects"] if item["name"] == "Field")
    assert field["evaluated"]["instance_count"] == 64
    assert [item["name"] for item in field["controls"]] == ["Count", "Realize for export"]

    varied = await work.call("blender_vary", {"file": blend, "object": "Field", "cases": {
        "small": {"Count": 2}, "large": {"Count": 32}, "bad": {"Missing": 1}}})
    cases = {item["name"]: item for item in varied["result"]["cases"]}
    assert cases["small"]["after_reopen"]["instance_count"] == 4
    assert cases["large"]["after_reopen"]["instance_count"] == 1024
    assert cases["large"]["changed_on_reopen"] == []
    assert "Unknown control" in cases["bad"]["error"]
    assert varied["result"]["all_cases_stable"] is False

    for kind in ("glb", "usd", "stl", "obj"):
        exported = await work.call("blender_export", {"file": blend, "format": kind})
        assert exported["status"] == "process_completed", exported.get("log_tail")
        assert exported["result"]["bytes"] > 0 and exported["result"]["loses"]
    glb = (await work.call("blender_export", {"file": blend, "format": "glb"}))["result"]
    assert glb["realized_for_export"] == ["Field"]
    assert glb["bounds_delta_m"] < 1e-5
    assert glb["readback"]["triangles"] == glb["evaluated"]["expanded_triangles"]

    shown = await work.call("blender_preview", {"file": blend, "views": ["iso", "top"], "width": 320, "height": 240,
                                                "object": "Field", "controls": {"Count": 4}})
    assert shown["result"]["evaluated"]["instance_count"] == 16
    assert shown["result"]["label"] == "exact review view"
    assert len(shown["_images"]) == 2

    capabilities = await work.call("blender_capabilities", {})
    assert "export_format" in capabilities["result"]["gltf_export_properties"]
    assert next(item for item in (await work.call("blender_inspect", {"file": blend}))["artifacts"])
    assert work.root.joinpath(blend).exists() and source_hash == _sha(work.root / blend)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_blender
@pytest.mark.asyncio
async def test_real_blender_guard_refuses_ordinary_escapes(tmp_path, monkeypatch):
    home = tmp_path / "member"
    home.mkdir()
    (home / "secret.txt").write_text("private")
    monkeypatch.setattr(Path, "home", classmethod(lambda _: home))
    work = BlenderTools(root=home / "nuluca" / "work", env={**os.environ, "NULUCA_BLENDER_PATH": REAL_BLENDER})
    await work.call("blender_write", {"file": "hostile.py", "source": HOSTILE})
    report = await work.call("blender_run", {"script": "hostile.py"})
    assert report["status"] == "process_completed", report.get("log_tail")
    outcome = json.loads((work.root / "blender-runs" / report["run"] / "artifacts" / "outcome.json").read_text())
    assert outcome.pop("work") == "allowed"
    assert set(outcome.values()) == {"refused"}, outcome
    assert (home / "secret.txt").read_text() == "private"
    assert not (home / "planted.txt").exists()


@needs_blender
def test_real_blender_leaves_when_the_server_dies(tmp_path):
    """A child left behind by a crashed server exits on its own."""
    script = tmp_path / "server.py"
    script.write_text(f'''
import asyncio, os, sys
from pathlib import Path
from nuluca_modeling_connect.blender import BlenderTools
async def main():
    work = BlenderTools(root=Path({str(tmp_path / "work")!r}), env={{**os.environ, "NULUCA_BLENDER_PATH": {REAL_BLENDER!r}}})
    await work.call("blender_write", {{"file": "spin.py", "source": {SPIN!r}}})
    report = await work.call("blender_run", {{"script": "spin.py", "wait_seconds": 0}})
    print(work._runs[report["run"]].process.pid, flush=True)
    os._exit(9)
asyncio.run(main())
''')
    result = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=60)
    pid = int(result.stdout.strip())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not state or state.startswith("Z"):
            break
        time.sleep(0.25)
    assert not state or state.startswith("Z"), state
