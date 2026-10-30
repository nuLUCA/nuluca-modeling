"""MCP stdio transport whose tools come only from the granted C9 catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib.metadata import version

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

from .blender import BlenderTools, blender_tools
from .bridge import BridgeError
from .client import CloudClient, ConnectError
from .local import LocalTools, local_tools
from .setup import CONFIGURED_CLIENTS, OTHER_CLIENT, SERVICES, SetupError, SetupOptions, detected_clients, setup, uninstall
from .workbench import Workbench, workbench_tools


def make_server(client: CloudClient | None, local: LocalTools | None = None,
                workbench: Workbench | None = None, blender: BlenderTools | None = None) -> Server:
    server = Server("nuluca-modeling")
    local = local or LocalTools()
    local_by_name = {item["name"]: item for item in local_tools()}
    workbench = workbench or Workbench(client)
    workbench_by_name = {item["name"]: item for item in workbench_tools()}
    blender = blender or BlenderTools()
    blender_by_name = {item["name"]: item for item in blender_tools()}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        cloud = [Tool(
            name=item["mcp_tool_name"], description=item.get("description") or item["label"],
            inputSchema=item["request_schema"],
        ) for item in (await client.discover() if client is not None else [])]
        status = Tool(name="nuluca_status", description="Check this nuLUCA connection and local Rhino bridge.",
                      inputSchema={"type": "object", "properties": {}})
        return [status] + cloud + [Tool(**item) for item in (*local_by_name.values(), *workbench_by_name.values(),
                                                             *blender_by_name.values())]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
        try:
            if name == "nuluca_status":
                granted = ([item["mcp_tool_name"] for item in await client.discover()]
                           if client is not None else [])
                bridge = await local.call("canvas_status", {})
                result = {"product": "nuluca", "granted_tools": granted,
                          "bridge": {key: bridge.get(key) for key in
                                     ("status", "protocol", "plugin_version", "exec_enabled", "document_open")}}
            elif name in local_by_name:
                result = await local.call(name, arguments)
            elif name in workbench_by_name:
                result = await workbench.call(name, arguments)
            elif name in blender_by_name:
                result = await blender.call(name, arguments)
            else:
                if client is None:
                    raise ConnectError("This local baseline has no cloud tools.")
                result = await client.call(name, arguments)
        except (ConnectError, BridgeError) as exc:
            result = {"error": {"code": str(exc)}}
        images = [ImageContent(type="image", mimeType="image/png", data=item)
                  for item in result.pop("_images", [])]
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False)), *images]

    return server


async def _run() -> None:
    client = None if os.environ.get("NULUCA_MODELING_BASELINE") == "1" else CloudClient()
    blender = BlenderTools()
    server = make_server(client, blender=blender)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await asyncio.to_thread(blender.close)
        if client is not None:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="nuluca-modeling")
    parser.add_argument("command", nargs="?", choices=["serve", "setup"], default="serve")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--plugin-source", help="Local plugin ZIP or HTTPS URL")
    parser.add_argument("--checksums-source", help="Local or HTTPS SHA256SUMS (defaults beside the ZIP)")
    parser.add_argument("--client", action="append", choices=[*CONFIGURED_CLIENTS, OTHER_CLIENT], dest="clients",
                        help="Client to configure. Use other for any different MCP client: setup prints its entry")
    parser.add_argument("--environment", choices=list(SERVICES),
                        help="nuLUCA service to use (default production, or NULUCA_AGENT_CONNECT_URL)")
    parser.add_argument("--always-allow-scripts", action=argparse.BooleanOptionalAction, default=None,
                        help="Let canvas_import_scripts run with no client prompt, across sessions")
    parser.add_argument("--always-allow-tools", action=argparse.BooleanOptionalAction, default=None,
                        help="Let every nuLUCA tool except script imports run with no client prompt, across sessions")
    parser.add_argument("--package-spec", help="Package path or URL the client should start instead of PyPI")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.version:
        print(version("nuluca-modeling"))
        return
    if args.command == "setup":
        try:
            if args.uninstall:
                result = uninstall(dry_run=args.dry_run)
            else:
                key = os.environ.get("NULUCA_AGENT_CONNECT_KEY", "")
                result = setup(SetupOptions(source=args.plugin_source, checksums=args.checksums_source,
                                            key=key, clients=tuple(args.clients or detected_clients()),
                                            dry_run=args.dry_run, environment=args.environment,
                                            package_spec=args.package_spec,
                                            always_allow_scripts=args.always_allow_scripts,
                                            always_allow_tools=args.always_allow_tools))
        except (SetupError, OSError) as exc:
            print(f"nuluca-modeling setup: {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))
        return
    asyncio.run(_run())
