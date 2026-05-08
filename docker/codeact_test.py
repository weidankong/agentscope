# -*- coding: utf-8 -*-
"""Test: assuming tool_server is already running, start codebox container,
then call run_ipython_cell which uses call_tool to invoke Write/Read
through the tool_server.
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

CODEBOX_IMAGE = "agentscope/codebox:dev"
CODEBOX_PORT = 8766


async def main() -> None:
    client = docker.from_env()

    # start codebox docker container
    client.images.get(CODEBOX_IMAGE)
    codebox_container = client.containers.run(
        image=CODEBOX_IMAGE,
        detach=True,
        ports={f"{CODEBOX_PORT}/tcp": ("127.0.0.1", CODEBOX_PORT)},
        extra_hosts={"host.docker.internal": "host-gateway"},
    )
    codebox_container.reload()
    print(f"Started codebox container: {codebox_container.id[:12]}")

    # wait for MCP server to be ready
    await asyncio.sleep(5)

    try:
        config = SandboxConfig(
            backend=LocalBackendParams(),
            mcp_gateway=McpGatewayConfig(enabled=True),
            mcp_servers=[
                McpServerConfig(
                    name="codebox-tools",
                    transport="streamable_http",
                    url=f"http://127.0.0.1:{CODEBOX_PORT}/mcp",
                ),
            ],
        )

        async with Sandbox(config) as sandbox:
            tools = await sandbox.list_tools()
            print(f"Codebox tools: {[t.name for t in tools]}")

            # execute code by run_ipython_cell()
            #   code includes call_tool(Write, {file_path: /workspace/test.txt, content: hello})
            #                 call_tool(Read, {file_path: /workspace/test.txt})
            #   return the Read content
            code1 = """
import httpx

def call_tool(tool_name, **kwargs):
    resp = httpx.post(
        "http://host.docker.internal:8767/call_tool",
        json={"tool_name": tool_name, "args": kwargs},
    )
    return resp.json()
"""
            code2 = """
call_tool("Write", file_path="/workspace/test.txt", content="hello")
call_tool("Read", file_path="/workspace/test.txt")
"""
            result = await sandbox.call_tool("run_ipython_cell", {"code": code1})
            print(f"\nrun_ipython_cell result:\n{result}")

            result = await sandbox.call_tool("run_ipython_cell", {"code": code2})
            print(f"\nrun_ipython_cell result:\n{result}")

    finally:
        codebox_container.stop()
        codebox_container.remove()
        print("\nCodebox container stopped and removed.")
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
