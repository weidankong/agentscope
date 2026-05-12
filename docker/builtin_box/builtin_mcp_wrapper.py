# -*- coding: utf-8 -*-
"""StreamableHTTP MCP server that exposes all AgentScope builtin tools.

Usage:
    python builtin_mcp_wrapper.py [--port PORT] [--host HOST]

Starts a single MCP server on ``http://<host>:<port>/mcp`` that aggregates
all six builtin tools: Bash, Read, Write, Edit, Glob, Grep.

Examples:
    # Start inside a Docker container:
    docker exec -d <container> python3 /agentscope_runtime/builtin_mcp_wrapper.py

    # Custom port / host:
    docker exec -d <container> python3 /agentscope_runtime/builtin_mcp_wrapper.py --port 9000 --host 0.0.0.0

    # Connect from host (port mapped via ``docker run -p 8765:8765``):
    #   streamable_http client at http://localhost:8765/mcp
"""

import argparse
import importlib
import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool as McpToolDef,
)
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn


# ---------------------------------------------------------------------------
# Tool class registry — all builtin tools in one server
# ---------------------------------------------------------------------------

_BUILTIN_TOOLS = [
    ("agentscope.tool._builtin._bash", "Bash"),
    ("agentscope.tool._builtin._read", "Read"),
    ("agentscope.tool._builtin._write", "Write"),
    ("agentscope.tool._builtin._edit", "Edit"),
    ("agentscope.tool._builtin._glob", "Glob"),
    ("agentscope.tool._builtin._grep", "Grep"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_to_str(chunk: Any) -> str:
    """Convert a ToolChunk to a plain string for MCP transport."""
    from agentscope.message import TextBlock

    parts = []
    for block in chunk.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)

    text = "\n".join(parts) if parts else ""

    state = getattr(chunk, "state", None)
    if state in ("error", "ERROR"):
        return f"[ERROR] {text}"
    return text


async def _call_tool(tool: Any, arguments: dict[str, Any]) -> str:
    """Invoke a ToolBase instance and flatten the result to a string."""
    from agentscope.tool._response import ToolChunk

    result = tool(**arguments)

    if inspect.isasyncgen(result):
        parts = []
        async for chunk in result:
            parts.append(_chunk_to_str(chunk))
        return "\n".join(parts)

    result = await result

    if isinstance(result, ToolChunk):
        return _chunk_to_str(result)

    parts = []
    for chunk in result:
        parts.append(_chunk_to_str(chunk))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


def _build_server() -> tuple[Server, dict[str, Any]]:
    """Create an MCP Server with all builtin tools registered.

    Returns:
        (server, tools_map) where tools_map is ``{tool_name: tool_instance}``.
    """
    server = Server("agentscope-builtin-tools")
    tools_map: dict[str, Any] = {}
    tool_defs: list[McpToolDef] = []

    for module_path, class_name in _BUILTIN_TOOLS:
        mod = importlib.import_module(module_path)
        tool_cls = getattr(mod, class_name)
        tool = tool_cls()
        tools_map[tool.name] = tool
        tool_defs.append(
            McpToolDef(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
            ),
        )

    @server.list_tools()
    async def list_tools() -> list[McpToolDef]:
        return tool_defs

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        tool = tools_map.get(name)
        if tool is None:
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Unknown tool: {name}",
                )],
                isError=True,
            )
        try:
            text = await _call_tool(tool, arguments)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
            )
        except Exception as e:
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error: {e}",
                )],
                isError=True,
            )

    return server, tools_map


def _create_app() -> Starlette:
    """Build the Starlette ASGI app that serves the MCP server."""
    mcp_server, _ = _build_server()
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
        description="Start a StreamableHTTP MCP server exposing all AgentScope builtin tools",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Listen port (default: 8765)",
    )
    args = parser.parse_args()

    app = _create_app()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
