# -*- coding: utf-8 -*-
"""Test: launch a builtin-box Docker sandbox and verify tools via the
MCPGateway (StreamableHTTP transport).

The container runs ``builtin_mcp_wrapper.py`` which aggregates all six
builtin tools behind a single ``/mcp`` endpoint.  The test uses
``Sandbox.list_tools`` / ``Sandbox.call_tool`` — the gateway connects
to the HTTP MCP server via port mapping automatically.
"""
import asyncio

import docker

from agentscope.sandbox import (
    Sandbox,
    SandboxConfig,
    LocalBackendParams,
    McpServerConfig,
    McpGatewayConfig,
)

IMAGE = "agentscope/builtin-box:dev"
MCP_CONTAINER_PORT = 8765
MCP_HOST_PORT = 8765


async def main() -> None:
    # start a docker with given image, get docker container id
    client = docker.from_env()
    client.images.get(IMAGE)
    container = client.containers.run(
        image=IMAGE,
        detach=True,
        ports={f"{MCP_CONTAINER_PORT}/tcp": ("127.0.0.1", MCP_HOST_PORT)},
    )
    container.reload()
    print(f"Started container: {container.id[:12]}")

    # wait for MCP server to be ready (poll until port responds)
    await asyncio.sleep(5)

    try:
        # start sandbox with LocalBackendParams, connecting to the
        # MCP server already running inside the Docker container
        config = SandboxConfig(
            backend=LocalBackendParams(),
            mcp_gateway=McpGatewayConfig(enabled=True),
            mcp_servers=[
                McpServerConfig(
                    name="builtin-tools",
                    transport="streamable_http",
                    url=f"http://127.0.0.1:{MCP_HOST_PORT}/mcp",
                ),
            ],
        )

        async with Sandbox(config) as sandbox:
            # List tools via the gateway
            tools = await sandbox.list_tools()
            print(f"\n{'='*60}")
            print(f"Found {len(tools)} tools:")
            print(f"{'='*60}\n")
            for tool in tools:
                print(f"  - {tool.name}")
                if tool.description:
                    desc = tool.description.split("\n")[0][:80]
                    print(f"    {desc}")
                print()

            # Call the Read tool
            result = await sandbox.call_tool("Read", {"file_path": "/agentscope_runtime/entrypoint.py"})
            print(f"\nRead result:\n{result}")

            # Call the Bash tool
            result = await sandbox.call_tool(
                "Bash",
                {"command": "ls -lh /agentscope_runtime/"},
            )
            print(f"\nBash result:\n{result}")

            # Write and read a file
            result = await sandbox.call_tool(
                "Write",
                {
                    "file_path": "/workspace/hello.txt",
                    "content": "Hello from Docker sandbox!",
                },
            )
            print(f"\nWrite result:\n{result}")
            result = await sandbox.call_tool(
                "Read",
                {"file_path": "/workspace/hello.txt"},
            )
            print(f"\nFile content:\n{result}")

            # Edit the file
            result = await sandbox.call_tool(
                "Edit",
                {
                    "file_path": "/workspace/hello.txt",
                    "old_string": "Hello from Docker sandbox!",
                    "new_string": "Hello from edited Docker sandbox!",
                },
            )
            print(f"\nEdit result:\n{result}")

            result = await sandbox.call_tool(
                "Read",
                {"file_path": "/workspace/hello.txt"},
            )
            print(f"\nAfter edit:\n{result}")

    finally:
        # close docker
        container.stop()
        container.remove()
        client.close()
        print("\nContainer stopped and removed.")


if __name__ == "__main__":
    asyncio.run(main())
