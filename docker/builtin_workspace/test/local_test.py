"""Smoke tests for builtin_mcp_server.

Runs the same test suite over stdio transport only.
The builtin MCP server is stdio-only — HTTP/SSE are handled by
the in-container gateway, not by this server directly.

Run from repo root::

    python docker/builtin_workspace/test/local_test.py
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logging.getLogger("mcp").setLevel(logging.WARNING)

SERVER_SCRIPT = "docker/builtin_workspace/builtin_mcp_server.py"


@asynccontextmanager
async def stdio_session() -> AsyncGenerator[ClientSession, None]:
    """Subprocess stdio server + stdio client."""
    params = StdioServerParameters(
        command="python",
        args=[SERVER_SCRIPT],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ---------------------------------------------------------------------------
# Shared test cases
# ---------------------------------------------------------------------------


async def test_list_tools(s: ClientSession) -> None:
    result = await s.list_tools()
    names = {t.name for t in result.tools}
    assert names == {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}, f"got {names}"

    by_name = {t.name: t for t in result.tools}
    # Check descriptions are non-empty
    for name in names:
        assert by_name[name].description, f"{name} has empty description"

    # Check input_schema has required properties
    for name in ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]:
        schema = by_name[name].inputSchema
        assert schema["type"] == "object", f"{name} schema type is not object"
        assert "properties" in schema, f"{name} schema missing properties"

    assert "command" in by_name["Bash"].inputSchema["properties"]
    assert "file_path" in by_name["Read"].inputSchema["properties"]
    assert "file_path" in by_name["Write"].inputSchema["properties"]
    assert "old_string" in by_name["Edit"].inputSchema["properties"]
    assert "pattern" in by_name["Glob"].inputSchema["properties"]
    assert "pattern" in by_name["Grep"].inputSchema["properties"]


async def test_bash(s: ClientSession) -> None:
    r = await s.call_tool("Bash", {"command": "echo hello"})
    assert not r.isError, f"Bash error: {r.content}"
    assert "hello" in r.content[0].text, f"unexpected: {r.content[0].text}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("list_tools", test_list_tools),
    ("bash", test_bash),
]


async def main() -> None:
    print("\n=== stdio ===")
    async with stdio_session() as session:
        for name, fn in TESTS:
            await fn(session)
            print(f"  {name}: OK")

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
