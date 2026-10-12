"""Export the actual MCP schemas; no console connection or credentials needed."""
import asyncio
import json
from pathlib import Path

from unifi_network_mcp.server import mcp


async def main():
    tools = await mcp.list_tools()
    path = Path(__file__).resolve().parents[1] / 'docs/tool-definitions.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'server': 'UniFi Loom', 'tools': [tool.model_dump(exclude_none=True, by_alias=True) for tool in tools]}, indent=2) + '\n')
    print(f'Exported {len(tools)} real MCP definitions.')


if __name__ == '__main__':
    asyncio.run(main())
