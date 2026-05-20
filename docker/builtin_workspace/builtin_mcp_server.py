"""MCP server exposing agentscope builtin tools over stdio.

Why not ``mcp.add_tool()``?
  FastMCP's ``add_tool`` derives the JSON schema from the wrapper function
  signature via ``func_metadata``, then *validates* incoming arguments against
  the resulting pydantic model at call time (``call_fn_with_arg_validation``).
  Agentscope tools like Bash have non-identifier parameter names (``-A``,
  ``-B``) that cannot appear in a pydantic model, so the validation step
  rejects them.

  Instead, we build ``Tool`` objects directly with the original
  ``input_schema`` and a thin ``_PassthroughTool`` subclass that calls
  ``fn(**arguments)`` directly, skipping pydantic validation.
"""

import asyncio
from typing import Any

from mcp.server import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import func_metadata

from agentscope.tool import Bash, Read, Write, Edit, Glob, Grep
from agentscope.tool import ToolChunk
from agentscope.message import TextBlock


BUILTIN_TOOLS = (Bash, Read, Write, Edit, Glob, Grep)


async def _call_and_flatten(tool_instance: Any, **kwargs: Any) -> str:
    """Invoke an agentscope tool and flatten ToolChunk(s) to a string."""
    result = tool_instance(**kwargs)
    if asyncio.iscoroutine(result):
        result = await result

    chunks: list[ToolChunk] = []
    if hasattr(result, "__aiter__"):
        async for chunk in result:
            chunks.append(chunk)
    else:
        chunks.append(result)

    parts: list[str] = []
    for chunk in chunks:
        for block in chunk.content:
            if isinstance(block, TextBlock) or hasattr(block, "text"):
                parts.append(block.text)
        if getattr(chunk, "state", None) is not None and str(chunk.state).lower() == "error":
            if parts:
                parts[-1] = f"[ERROR] {parts[-1]}"
    return "\n".join(parts) if parts else ""


class _PassthroughTool(Tool):
    """A Tool that calls ``fn(**arguments)`` directly, skipping pydantic
    validation of incoming arguments.

    Needed because agentscope tools may have parameter names (e.g. ``-A``)
    that are not valid Python identifiers and therefore cannot be modelled
    by pydantic.  We preserve the original ``input_schema`` for discovery
    but bypass validation at call time.
    """

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        try:
            result = await self.fn(**arguments)
            if convert_result:
                result = self.fn_metadata.convert_result(result)
            return result
        except Exception as e:
            from mcp.shared.exceptions import McpError
            if isinstance(e, McpError):
                raise
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError(f"Error executing tool {self.name}: {e}") from e


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_tool(server: FastMCP, tool_cls: type) -> None:
    """Register a single agentscope tool as an MCP tool."""
    inst = tool_cls()

    async def handler(**kw: Any) -> str:
        return await _call_and_flatten(inst, **kw)

    handler.__name__ = inst.name

    server._tool_manager._tools[inst.name] = _PassthroughTool(
        fn=handler,
        name=inst.name,
        description=inst.description,
        parameters=inst.input_schema,
        fn_metadata=func_metadata(handler),
        is_async=True,
    )


def create_server(name: str = "builtin_tools") -> FastMCP:
    """Create an MCP server with all agentscope builtin tools registered."""
    server = FastMCP(name)
    for cls in BUILTIN_TOOLS:
        register_tool(server, cls)
    return server


mcp = create_server()

if __name__ == "__main__":
    mcp.run(transport="stdio")
