"""Member canvas calls cross the live bridge contract, never an exec route."""

import json
import os
from pathlib import Path

import httpx
import pytest
from nuluca_modeling_connect.bridge import BridgeClient, BridgeError
from nuluca_modeling_connect.local import LocalTools, local_tools


def discovery(tmp_path: Path, *, pid: int | None = None) -> Path:
    tmp_path.chmod(0o700)
    path = tmp_path / "bridge-6500.json"
    path.write_text(json.dumps({"protocol": "jsontogh-bridge/1", "port": 6500,
                                "token": "a" * 64, "pid": pid or os.getpid(),
                                "instance_id": "bridge-1", "exec_enabled": False}))
    path.chmod(0o600)
    return path


@pytest.mark.asyncio
async def test_member_tools_use_token_and_no_exec(tmp_path):
    discovery(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "stair.json").write_text(json.dumps({"Version": "1.3", "Components": []}))
    requests = []

    def reply(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer " + "a" * 64
        assert request.headers.get("origin") is None
        if request.url.path == "/status":
            return httpx.Response(200, json={"protocol": "jsontogh-bridge/1",
                                             "instance_id": "bridge-1", "document_open": True})
        return httpx.Response(200, json={"success": True, "errors": []})

    local = LocalTools(BridgeClient(root=tmp_path, transport=httpx.MockTransport(reply)), root=work)
    assert (await local.call("canvas_status", {}))["document_open"]
    assert (await local.call("canvas_import", {"file": "stair.json"}))["success"]
    assert requests[-1].url.path == "/import"
    assert requests[-1].url.params["clear"] == "true"
    assert json.loads(requests[-1].content)["Version"] == "1.3"
    assert all("run_python" not in item["name"] and "run_command" not in item["name"]
               for item in local_tools())
    with pytest.raises(BridgeError, match="Unknown"):
        await local.call("run_python", {"script": "print(1)"})
    with pytest.raises(BridgeError, match="inside"):
        await local.call("canvas_import", {"file": "../stair.json"})


@pytest.mark.asyncio
async def test_dead_discovery_and_changed_instance_are_refused(tmp_path):
    path = discovery(tmp_path, pid=99999999)
    bridge = BridgeClient(root=tmp_path)
    with pytest.raises(BridgeError, match="No running Rhino bridge"):
        await bridge.call("GET", "/status")
    discovery(tmp_path)
    def reply(_request):
        return httpx.Response(200, json={"protocol": "jsontogh-bridge/1",
                                         "instance_id": "different"})
    bridge = BridgeClient(root=tmp_path, transport=httpx.MockTransport(reply))
    with pytest.raises(BridgeError, match="changed"):
        await bridge.call("GET", "/status")
    path.chmod(0o644)
    with pytest.raises(BridgeError, match="No running Rhino bridge"):
        await bridge.call("GET", "/status")


@pytest.mark.asyncio
async def test_multiple_live_bridges_require_a_port(tmp_path, monkeypatch):
    monkeypatch.delenv("NULUCA_GH_BRIDGE_PORT", raising=False)
    discovery(tmp_path)
    second = tmp_path / "bridge-6501.json"
    second.write_text(json.dumps({"protocol": "jsontogh-bridge/1", "port": 6501,
                                  "token": "b" * 64, "pid": os.getpid(),
                                  "instance_id": "bridge-2", "exec_enabled": False}))
    second.chmod(0o600)
    with pytest.raises(BridgeError, match="Select one running Rhino bridge"):
        await BridgeClient(root=tmp_path).call("GET", "/status")


def canvas(tmp_path, *, status=None, routes=None):
    discovery(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "stair.json").write_text(json.dumps({"Version": "1.3", "Components": []}))
    requests = []

    def reply(request):
        requests.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json={"protocol": "jsontogh-bridge/1", "instance_id": "bridge-1",
                                             "document_open": True, **(status or {})})
        response = (routes or {}).get(request.url.path)
        return response or httpx.Response(200, json={"success": True, "errors": []})

    return LocalTools(BridgeClient(root=tmp_path, transport=httpx.MockTransport(reply)), root=work), requests


@pytest.mark.asyncio
async def test_import_warns_before_clearing_a_canvas_with_objects(tmp_path):
    local, requests = canvas(tmp_path, status={"component_count": 3, "parameter_count": 2})
    result = await local.call("canvas_import", {"file": "stair.json"})

    assert result["warning"] == "canvas_not_empty"
    assert result["object_count"] == 5
    assert "Ask the member" in result["message"]
    assert all(request.url.path != "/import" for request in requests)


@pytest.mark.asyncio
async def test_empty_canvas_imports_without_asking(tmp_path):
    local, requests = canvas(tmp_path, status={"component_count": 0, "parameter_count": 0})
    assert (await local.call("canvas_import", {"file": "stair.json"}))["success"]
    assert requests[-1].url.path == "/import"


@pytest.mark.asyncio
async def test_member_choice_decides_whether_the_canvas_is_cleared(tmp_path):
    local, requests = canvas(tmp_path, status={"component_count": 3})
    await local.call("canvas_import", {"file": "stair.json", "replace_canvas": True})
    assert requests[-1].url.params["clear"] == "true"
    await local.call("canvas_import", {"file": "stair.json", "add_to_canvas": True})
    assert requests[-1].url.params["clear"] == "false"
    assert all(request.url.path != "/status" for request in requests)


@pytest.mark.asyncio
async def test_truthy_strings_do_not_authorize_clearing(tmp_path):
    local, requests = canvas(tmp_path, status={"component_count": 1})
    result = await local.call("canvas_import", {"file": "stair.json", "replace_canvas": "yes"})
    assert result["warning"] == "canvas_not_empty"
    assert all(request.url.path != "/import" for request in requests)


@pytest.mark.asyncio
async def test_preflight_sends_the_definition_and_reports_what_is_missing(tmp_path):
    report = {"success": True, "ready": False, "missing": [{"type": "Zombie Solver", "likely_plugin": "Kangaroo2"}]}
    local, requests = canvas(tmp_path, routes={"/preflight": httpx.Response(200, json=report)})
    result = await local.call("canvas_preflight", {"file": "stair.json"})

    assert result == report
    assert requests[-1].method == "POST"
    assert json.loads(requests[-1].content)["Version"] == "1.3"


@pytest.mark.asyncio
async def test_preflight_on_an_older_plugin_explains_itself(tmp_path):
    local, _ = canvas(tmp_path, routes={"/preflight": httpx.Response(404, json={"error": "Not found"})})
    result = await local.call("canvas_preflight", {"file": "stair.json"})
    assert result["error"] == "preflight_unavailable"
    assert "Run setup again" in result["message"]


@pytest.mark.asyncio
async def test_installed_plugins_are_readable(tmp_path):
    local, requests = canvas(tmp_path, routes={"/plugins": httpx.Response(200, json={"success": True, "plugins": []})})
    assert (await local.call("canvas_plugins", {}))["success"]
    assert requests[-1].url.path == "/plugins"


def test_tool_descriptions_tell_the_agent_about_clearing_and_preflight():
    tools = {item["name"]: item for item in local_tools()}
    assert "canvas_not_empty" in tools["canvas_import"]["description"]
    assert set(tools["canvas_import"]["inputSchema"]["properties"]) == {"file", "replace_canvas", "add_to_canvas"}
    assert tools["canvas_preflight"]["inputSchema"]["required"] == ["file"]
