# -*- coding: utf-8 -*-
"""StreamableHTTP MCP server that exposes codebox tools: run_ipython_cell and
run_shell_command.

Usage:
    python codebox_mcp_server.py [--port PORT] [--host HOST]

Starts an MCP server on ``http://<host>:<port>/mcp`` that provides two tools
for executing code and shell commands inside a stateful IPython kernel.

Examples:
    # Start inside a Docker container:
    docker exec -d <container> python3 /agentscope_runtime/codebox_mcp_server.py

    # Custom port / host:
    docker exec -d <container> python3 /agentscope_runtime/codebox_mcp_server.py --port 9000 --host 0.0.0.0

    # Connect from host (port mapped via ``docker run -p 8766:8766``):
    #   streamable_http client at http://localhost:8766/mcp
"""

import argparse
import traceback
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

from run_ipython_cell import DEFINITION as IPYTHON_DEF, run as run_ipython_cell
from run_shell_command import DEFINITION as SHELL_DEF, run as run_shell_command

_TOOLS = {
    "run_ipython_cell": (IPYTHON_DEF, run_ipython_cell),
    "run_shell_command": (SHELL_DEF, run_shell_command),
}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


def _build_server() -> Server:
    """Create an MCP Server with both codebox tools registered."""
    server = Server("agentscope-codebox-tools")

    @server.list_tools()
    async def list_tools():
        return [defn for defn, _ in _TOOLS.values()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        entry = _TOOLS.get(name)
        if entry is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

        _, handler = entry

        # Validate required arguments
        if name == "run_ipython_cell" and not arguments.get("code"):
            return CallToolResult(
                content=[TextContent(type="text", text="Code is required.")],
                isError=True,
            )
        if name == "run_shell_command" and not arguments.get("command"):
            return CallToolResult(
                content=[TextContent(type="text", text="Command is required.")],
                isError=True,
            )

        try:
            return await handler(**arguments)
        except Exception as e:
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error: {e}\n{traceback.format_exc()}",
                )],
                isError=True,
            )

    return server


def _create_app() -> Starlette:
    """Build the Starlette ASGI app that serves the MCP server."""
    mcp_server = _build_server()
    session_manager = StreamableHTTPSessionManager(app=mcp_server)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Mount("/", app=session_manager.handle_request),
        ],
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start a StreamableHTTP MCP server exposing codebox tools",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Listen port (default: 8766)",
    )
    args = parser.parse_args()

    app = _create_app()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
