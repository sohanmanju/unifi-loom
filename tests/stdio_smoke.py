"""Actual MCP protocol smoke test. No UniFi calls or credentials required."""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    env = {**os.environ, "UNIFI_URL": "https://console.invalid", "UNIFI_ALLOW_WRITES": "false",
           "UNIFI_INTERNAL_ENABLED": "false", "UNIFI_API_KEY": "", "UNIFI_USERNAME": "", "UNIFI_PASSWORD": "",
           "UNIFI_SPEC_FILE": "", "UNIFI_INTERNAL_MANIFEST": "", "UNIFI_CA_CERT": "",
           "UNIFI_TLS_SERVER_NAME": "", "UNIFI_INTERNAL_AUTH": "session"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "unifi_network_mcp.server"], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 28
            radio_tool = next(tool for tool in tools.tools if tool.name == "prepare_ap_radio_change")
            assert "settings" in radio_tool.inputSchema["properties"]
            invalid = await session.call_tool("prepare_ap_radio_change", {
                "device_id": "ap1", "radio_name": "wifi0", "settings": {}, "reason": "Test"})
            assert invalid.isError
            result = await session.call_tool("list_operations", {"adapter": "official"})
            assert not result.isError
            content = json.loads(result.content[0].text)
            assert len(content["operations"]) == 73
            snapshot = json.loads((Path(__file__).parents[1] / "examples/sample-snapshot.json").read_text())
            result = await session.call_tool("diagram_from_snapshot", {"snapshot": snapshot, "include_clients": True})
            assert not result.isError
            diagram = json.loads(result.content[0].text)
            assert "flowchart TD" in diagram["mermaid"]
            denied = await session.call_tool("apply_change", {"plan_id": "no-plan"})
            assert denied.isError
            print("MCP stdio smoke passed: 28 tools, 73 official operations, offline Mermaid, write denial")


if __name__ == "__main__":
    asyncio.run(main())
