"""
Smoke test for ``agentscope.workspace.DockerWorkspace``.

Requires ``docker`` (docker-py) and a running Docker daemon.

Run from repo root::

    python test_docker_workspace.py
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

from agentscope.workspace import DockerWorkspace, WorkspaceBase, DockerWorkspaceManager
from agentscope.workspace import MCPServerConfig

PYTHON_IMAGE = "agentscope/builtin_workspace:latest"

BUILTIN_MCP_CONFIG = MCPServerConfig(
    name="builtin_tools",
    protocol="stdio",
    command="python",
    args=["/agentscope/builtin_mcp_server.py"],
)


MCP_SERVER_SCRIPT = textwrap.dedent("""\
    from mcp.server import FastMCP

    def greet(name: str) -> str:
        return f"Hello, {name}!"

    def add(a: int, b: int) -> int:
        return a + b

    mcp = FastMCP("Greet Server")
    mcp.tool(description="Greet someone by name.")(greet)
    mcp.tool(description="Add two integers.")(add)

    if __name__ == "__main__":
        mcp.run(transport="stdio")
""")


def _docker_daemon_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _make_skill_dir(base: str, name: str, desc: str) -> str:
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(f"---\nname: {name}\ndescription: {desc}\n---\n")
        f.write(f"# {name}\n\nSkill content.\n")
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_initialize_exec_close() -> None:
    """Create workspace, exec a command, verify, close."""
    # start docker WITHOUT built-in mcp_server
    ws = DockerWorkspace(image=PYTHON_IMAGE)
    assert isinstance(ws, WorkspaceBase)

    await ws.initialize()
    try:
        r = await ws._exec("echo hello")
        assert r.is_ok()
        assert b"hello" in r.stdout

        r = await ws._exec("pwd")
        assert r.is_ok()
        assert b"/workspace" in r.stdout
    finally:
        await ws.close()
    print("  OK")


async def test_read_write() -> None:
    """Write a file, read it back."""
    ws = DockerWorkspace(image=PYTHON_IMAGE)
    await ws.initialize()
    try:
        await ws._write("test.txt", b"hello-docker")
        data = await ws._read("test.txt")
        assert data == b"hello-docker", f"got: {data}"
    finally:
        await ws.close()
    print("  OK")


async def test_env_and_startup_commands() -> None:
    """Env vars and startup commands work."""
    ws = DockerWorkspace(
        image=PYTHON_IMAGE,
        env={"MY_VAR": "test_val"},
        startup_commands=['printf "%s" "$MY_VAR" > /workspace/env.txt'],
    )
    await ws.initialize()
    try:
        data = await ws._read("env.txt")
        assert data == b"test_val", f"got: {data}"
    finally:
        await ws.close()
    print("  OK")


async def test_workspace_interface() -> None:
    """list_tools, list_mcps, list_skills, get_instructions work."""
    ws = DockerWorkspace(image=PYTHON_IMAGE)
    await ws.initialize()
    try:
        instructions = await ws.get_instructions()
        assert "Docker" in instructions or "workspace" in instructions.lower()

        tools = await ws.list_tools()
        assert isinstance(tools, list)

        mcps = await ws.list_mcps()
        assert isinstance(mcps, list)
        assert len(mcps) == 0

        skills = await ws.list_skills()
        assert isinstance(skills, list)
        assert len(skills) == 0
    finally:
        await ws.close()
    print("  OK")


async def test_add_remove_skill() -> None:
    """Add a skill from host, list it, remove it."""
    ws = DockerWorkspace(image=PYTHON_IMAGE)
    await ws.initialize()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            sp = _make_skill_dir(tmpdir, "my_skill", "Test skill")
            await ws.add_skill(sp)

            skills = await ws.list_skills()
            assert len(skills) == 1, f"expected 1, got {len(skills)}"
            assert skills[0].name == "my_skill"

            await ws.remove_skill("my_skill")
            skills = await ws.list_skills()
            assert len(skills) == 0
    finally:
        await ws.close()
    print("  OK")


async def test_export_state() -> None:
    """export_state returns valid serialized state."""
    ws = DockerWorkspace(image=PYTHON_IMAGE)
    await ws.initialize()
    try:
        state = await ws.export_state()
        assert state.backend_type == "docker"
        assert "container_id" in state.payload
        assert "workspace_id" in state.payload
    finally:
        await ws.close()
    print("  OK")


async def test_async_context_manager() -> None:
    """async with DockerWorkspace works."""
    async with DockerWorkspace(image=PYTHON_IMAGE) as ws:
        r = await ws._exec("echo ctx_mgr")
        assert r.is_ok()
        assert b"ctx_mgr" in r.stdout
    print("  OK")


async def test_manager_basic() -> None:
    """DockerWorkspaceManager creates and tracks workspaces."""
    mgr = DockerWorkspaceManager(image=PYTHON_IMAGE)
    await mgr.initialize()
    try:
        ws = await mgr.create_workspace(
            user_id="user1",
            agent_id="agent1",
            session_id="session-001",
        )
        r = await ws._exec("echo managed")
        assert r.is_ok()
        assert b"managed" in r.stdout

        # Same workspace_id returns same workspace
        ws2 = await mgr.get_workspace(ws.workspace_id)
        assert ws is ws2
    finally:
        await mgr.close_all()
    print("  OK")


async def test_pool_acquire_release() -> None:
    """Pool: warm up, acquire, use, release, resize."""
    mgr = DockerWorkspaceManager(image=PYTHON_IMAGE)
    mgr.enable_pool(capacity=2)
    assert mgr.pool_enabled

    await mgr.initialize()
    try:
        await mgr.warm_up_pool()
        state = mgr.get_pool_state()
        assert state["capacity"] == 2
        assert state["free"] == 2
        assert state["in_use"] == 0

        ws1 = await mgr.acquire_from_pool()
        assert state["capacity"] == 2
        r = await ws1._exec("echo pool_ws1")
        assert r.is_ok()
        assert b"pool_ws1" in r.stdout

        ws2 = await mgr.acquire_from_pool()
        state = mgr.get_pool_state()
        assert state["free"] == 0
        assert state["in_use"] == 2

        await mgr.release_to_pool(ws1)
        state = mgr.get_pool_state()
        assert state["free"] == 1
        assert state["in_use"] == 1

        await mgr.release_to_pool(ws2)
        state = mgr.get_pool_state()
        assert state["free"] == 2
        assert state["in_use"] == 0

        # Resize up
        await mgr.resize_pool(3)
        state = mgr.get_pool_state()
        assert state["capacity"] == 3
        assert state["free"] == 3

        # Resize down
        await mgr.resize_pool(1)
        state = mgr.get_pool_state()
        assert state["capacity"] == 1
        assert state["free"] == 1
    finally:
        await mgr.close()
    print("  OK")


async def test_mcp_gateway() -> None:
    """Start gateway with a FastMCP server, verify tools are proxied."""
    ws = DockerWorkspace(
        image=PYTHON_IMAGE,
        startup_commands=[
            "cat > /tmp/greet_server.py << 'PYEOF'\n" + MCP_SERVER_SCRIPT + "PYEOF",
        ],
        mcp_servers=[
            MCPServerConfig(
                name="greet",
                protocol="stdio",
                command="python",
                args=["/tmp/greet_server.py"],
            ),
        ],
    )
    await ws.initialize()
    try:
        mcps = await ws.list_mcps()
        assert len(mcps) == 1, f"expected 1 MCP, got {len(mcps)}"

        gateway = mcps[0]
        tools = await gateway.list_tools()
        tool_names = [t.name for t in tools]
        assert "greet___greet" in tool_names, f"greet___greet not in {tool_names}"
        assert "greet___add" in tool_names, f"greet___add not in {tool_names}"

        greet_tool = await gateway.get_tool("greet___greet")
        result = await greet_tool(name="Docker")
        assert "Hello, Docker!" in str(result), f"unexpected: {result}"

        add_tool = await gateway.get_tool("greet___add")
        result = await add_tool(a=10, b=20)
        assert "30" in str(result), f"unexpected: {result}"
    finally:
        await ws.close()
    print("  OK")


async def test_builtin_mcp_tools() -> None:
    """Builtin MCP tools (Bash, Read, Write, Edit, Glob, Grep) are
    available via streamable-http inside the workspace-docker image."""
    ws = DockerWorkspace(image=PYTHON_IMAGE, mcp_servers=[BUILTIN_MCP_CONFIG])
    await ws.initialize()
    try:
        mcps = await ws.list_mcps()
        assert len(mcps) == 1, f"expected 1 gateway, got {len(mcps)}"

        gateway = mcps[0]
        tools = await gateway.list_tools()
        tool_names = [t.name for t in tools]

        # Builtin tools are prefixed with "builtin_tools___"
        for expected in ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]:
            prefixed = f"builtin_tools___{expected}"
            assert prefixed in tool_names, (
                f"{prefixed} not in {tool_names}"
            )

        # Call Bash tool through the gateway
        bash_tool = await gateway.get_tool("builtin_tools___Bash")
        result = await bash_tool(command="echo hello-builtin")
        assert "hello-builtin" in str(result), f"unexpected: {result}"

        # Call Write then Read through the gateway
        write_tool = await gateway.get_tool("builtin_tools___Write")
        result = await write_tool(
            file_path="/workspace/builtin_test.txt",
            content="line1\nline2\n",
        )
        assert "written" in str(result).lower(), f"unexpected: {result}"

        read_tool = await gateway.get_tool("builtin_tools___Read")
        result = await read_tool(file_path="/workspace/builtin_test.txt")
        assert "line1" in str(result), f"unexpected: {result}"
    finally:
        await ws.close()
    print("  OK")


async def test_mcp_gateway_dynamic_add_remove() -> None:
    """Start gateway, then dynamically add/remove an MCP server."""
    second_script = textwrap.dedent("""\
        from mcp.server import FastMCP

        def multiply(a: int, b: int) -> int:
            return a * b

        mcp = FastMCP("Math Server")
        mcp.tool(description="Multiply two integers.")(multiply)

        if __name__ == "__main__":
            mcp.run(transport="stdio")
    """)

    ws = DockerWorkspace(
        image=PYTHON_IMAGE,
        startup_commands=[
            "cat > /tmp/greet_server.py << 'PYEOF'\n" + MCP_SERVER_SCRIPT + "PYEOF",
            "cat > /tmp/math_server.py << 'PYEOF'\n" + second_script + "PYEOF",
        ],
        mcp_servers=[
            MCPServerConfig(
                name="greet",
                protocol="stdio",
                command="python",
                args=["/tmp/greet_server.py"],
            ),
        ],
    )
    await ws.initialize()
    try:
        gateway = (await ws.list_mcps())[0]
        tools_before = await gateway.list_tools()
        names_before = {t.name for t in tools_before}
        assert "greet___greet" in names_before

        await ws.add_mcp(
            MCPServerConfig(
                name="math",
                protocol="stdio",
                command="python",
                args=["/tmp/math_server.py"],
            ),
        )

        tools_after = await gateway.list_tools()
        names_after = {t.name for t in tools_after}
        assert "math___multiply" in names_after, f"math___multiply not in {names_after}"

        mul_tool = await gateway.get_tool("math___multiply")
        result = await mul_tool(a=6, b=7)
        assert "42" in str(result), f"unexpected: {result}"

        await ws.remove_mcp("math")
        tools_final = await gateway.list_tools()
        names_final = {t.name for t in tools_final}
        assert "math___multiply" not in names_final
        assert "greet___greet" in names_final
    finally:
        await ws.close()
    print("  OK")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    try:
        import docker  # noqa: F401
    except ImportError:
        print(
            "ERROR: ``docker`` (docker-py) is required. "
            "Install with: pip install docker",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _docker_daemon_ready():
        print(
            "Skipping: Docker CLI missing or daemon not running.",
        )
        print("=== SKIPPED ===")
        return

    tests = [
        ("test_initialize_exec_close", test_initialize_exec_close),
        ("test_read_write", test_read_write),
        ("test_env_and_startup_commands", test_env_and_startup_commands),
        ("test_workspace_interface", test_workspace_interface),
        ("test_add_remove_skill", test_add_remove_skill),
        ("test_export_state", test_export_state),
        ("test_async_context_manager", test_async_context_manager),
        ("test_manager_basic", test_manager_basic),
        ("test_mcp_gateway", test_mcp_gateway),
        ("test_builtin_mcp_tools", test_builtin_mcp_tools),
        ("test_mcp_gateway_dynamic_add_remove", test_mcp_gateway_dynamic_add_remove),
    ]

    for name, fn in tests:
        print(f"{name}:", end="")
        await fn()

    print("\n=== ALL DOCKER WORKSPACE TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
