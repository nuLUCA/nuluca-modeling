"""A member definition stays readable after each small edit."""

import json

import pytest
from nuluca_modeling_connect.bridge import BridgeError
from nuluca_modeling_connect.workbench import Workbench, workbench_tools


class Catalog:
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"component": {"category": "Curve", "subcategory": "Primitive",
                              "inputs": [{"name": "R"}], "outputs": [{"name": "C"}]}}


@pytest.mark.asyncio
async def test_incremental_definition_and_catalog_cache(tmp_path):
    catalog = Catalog()
    wb = Workbench(catalog, root=tmp_path)
    await wb.call("wb_new", {"file": "stair.json", "description": "Stair"})
    assert json.loads((tmp_path / "stair.json").read_text())["Components"] == []
    p = await wb.call("wb_add_parameter", {"file": "stair.json", "param_type": "Slider",
                                           "nickname": "Rise", "extra": {"Value": 2.8, "Min": 1, "Max": 4}})
    assert len(json.loads((tmp_path / "stair.json").read_text())["Parameters"]) == 1
    c = await wb.call("wb_add_component", {"file": "stair.json", "component_type": "Circle",
                                           "nickname": "Tread Circle"})
    await wb.call("wb_connect", {"file": "stair.json", "source_id": p["node_id"],
                                 "source_port": "Value", "target_id": c["node_id"],
                                 "target_port": "R"})
    await wb.call("wb_add_component", {"file": "stair.json", "component_type": "Circle",
                                       "nickname": "Second Circle"})
    assert len(catalog.calls) == 1
    assert len((await wb.call("wb_read", {"file": "stair.json"}))["Connections"]) == 1
    with pytest.raises(BridgeError, match="work directory"):
        await wb.call("wb_new", {"file": "../outside.json"})


@pytest.mark.asyncio
async def test_plain_bridge_baseline_has_no_catalog_dependency(tmp_path):
    component_schema = next(tool["inputSchema"] for tool in workbench_tools()
                            if tool["name"] == "wb_add_component")
    assert {"category", "subcategory", "nickname", "position"} <= component_schema["properties"].keys()
    wb = Workbench(None, root=tmp_path)
    await wb.call("wb_new", {"file": "plain.json"})
    with pytest.raises(BridgeError, match="category"):
        await wb.call("wb_add_component", {"file": "plain.json", "component_type": "Circle"})
    result = await wb.call("wb_add_component", {
        "file": "plain.json", "component_type": "Circle",
        "category": "Curve", "subcategory": "Primitive"})
    assert result["node_id"] == "circle"


@pytest.mark.asyncio
async def test_complete_definition_can_be_saved_for_one_bridge_import(tmp_path):
    wb = Workbench(None, root=tmp_path)
    definition = {"Version": "1.3", "Components": [], "Parameters": [],
                  "Connections": [], "Groups": []}
    result = await wb.call("wb_write", {"file": "complete.json", "script": definition})
    assert result["components"] == 0
    assert json.loads((tmp_path / "complete.json").read_text()) == definition
    with pytest.raises(BridgeError, match="complete JsonToGH"):
        await wb.call("wb_write", {"file": "complete.json", "script": {"Version": "1.3"}})
    assert json.loads((tmp_path / "complete.json").read_text()) == definition
