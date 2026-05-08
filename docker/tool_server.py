# -*- coding: utf-8 -*-
"""Tool server — a tiny HTTP server that proxies call_tool requests from inside
the codebox container to the Sandbox (which routes them to builtin-box).

Usage:
    python tool_server.py [--port PORT]

The codebox container sends POST /call_tool with JSON body:
    {"tool_name": "Write", "args": {"file_path": "/workspace/test.txt", "content": "hello"}}

The server forwards the call to ``sandbox.call_tool(tool_name, **args)`` and
returns the result as JSON.
"""

import argparse
import asyncio
from typing import Any

import docker as docker_lib
from aiohttp import web
from agentscope.sandbox import (
    Sandbox,
    SandboxConfig,
    LocalBackendParams,
    McpServerConfig,
    McpGatewayConfig,
)

BUILTIN_IMAGE = "agentscope/builtin-box:dev"
BUILTIN_PORT = 8765

# The single sandbox instance — connects to builtin-box.
_sandbox: Sandbox | None = None
_builtin_container = None


async def handle_call_tool(request: web.Request) -> web.Response:
    """Receive call_tool(tool_name, **kwargs) from inside the codebox container,
    respond with sandbox.call_tool(tool_name, **kwargs) which calls the builtin-box.
    """
    global _sandbox

    body = await request.json()
    tool_name = body.get("tool_name")
    args = body.get("args", {})

    print(f"[tool_server] Received call_tool: tool_name={tool_name}, args={args}")

    if not tool_name:
        return web.json_response(
            {"error": "tool_name is required"},
            status=400,
        )

    if _sandbox is None:
        return web.json_response(
            {"error": "Sandbox not initialized"},
            status=500,
        )

    try:
        result = await _sandbox.call_tool(tool_name, args)
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        elif hasattr(result, "__dict__"):
            result = str(result)
        return web.json_response({"result": result})
    except Exception as e:
        return web.json_response(
            {"error": f"{type(e).__name__}: {e}"},
            status=500,
        )


async def on_startup(app: web.Application) -> None:
    """Start the builtin-box container and connect the sandbox."""
    global _sandbox, _builtin_container

    client = docker_lib.from_env()
    client.images.get(BUILTIN_IMAGE)
    _builtin_container = client.containers.run(
        image=BUILTIN_IMAGE,
        detach=True,
        ports={f"{BUILTIN_PORT}/tcp": ("127.0.0.1", BUILTIN_PORT)},
    )
    _builtin_container.reload()
    print(f"Started builtin-box container: {_builtin_container.id[:12]}")

    # wait for MCP server to be ready
    await asyncio.sleep(5)

    config = SandboxConfig(
        backend=LocalBackendParams(),
        mcp_gateway=McpGatewayConfig(enabled=True),
        mcp_servers=[
            McpServerConfig(
                name="builtin-tools",
                transport="streamable_http",
                url=f"http://127.0.0.1:{BUILTIN_PORT}/mcp",
            ),
        ],
    )
    _sandbox = Sandbox(config)
    await _sandbox.start()
    tools = await _sandbox.list_tools()
    print(f"Sandbox started, connected to builtin-box. Tools: {[t.name for t in tools]}")


async def on_cleanup(app: web.Application) -> None:
    """Close the sandbox and stop the builtin-box container."""
    global _sandbox, _builtin_container

    if _sandbox is not None:
        await _sandbox.close()
        _sandbox = None
        print("Sandbox closed.")

    if _builtin_container is not None:
        _builtin_container.stop()
        _builtin_container.remove()
        print("Builtin-box container stopped and removed.")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/call_tool", handle_call_tool)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tool server — proxies call_tool from codebox to builtin-box",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8767,
        help="Listen port (default: 8767)",
    )
    args = parser.parse_args()

    app = create_app()
    web.run_app(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
