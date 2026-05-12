# -*- coding: utf-8 -*-
"""DockerBackendParams sandbox smoke test — launch builtin-box and list tools."""

import asyncio

from agentscope.sandbox import Sandbox
from agentscope.sandbox.config import (
    DockerBackendParams,
    McpGatewayConfig,
    McpServerConfig,
    SandboxConfig,
)

MCP_CONTAINER_PORT = 8765
MCP_HOST_PORT = 8765
IMAGE = "agentscope/builtin-box:dev"
HOST_DIR = "/mnt/disk1t/weidan.kong/work/agentscope/docker"
MOUNT_POINT = "/workspace/tools"


async def main() -> None:
    config = SandboxConfig(
        backend=DockerBackendParams(
            image=IMAGE,
            use_image_entrypoint=True,
            port_map={MCP_CONTAINER_PORT: MCP_HOST_PORT},
        ),
        mcp_gateway=McpGatewayConfig(enabled=True),
        mcp_servers=[
            McpServerConfig(
                name="builtin-tools",
                transport="streamable_http",
                url=f"http://127.0.0.1:{MCP_HOST_PORT}/mcp",
            ),
        ],
        volumes={HOST_DIR: MOUNT_POINT},
        exposed_ports=[MCP_CONTAINER_PORT],
    )

    async with Sandbox(config) as sandbox:
        tools = await sandbox.list_tools()
        print(f"\nTools ({len(tools)}):")
        for t in tools:
            print(f"  - {t.name}")


if __name__ == "__main__":
    asyncio.run(main())
