"""End-to-end test: Agent using DockerWorkspace with builtin MCP tools.

Creates a minimal agent backed by the ``agentscope/builtin_workspace`` Docker
image, connects to the in-container MCP gateway, and verifies that the agent
can use tools (Bash, Write, Read) to complete a task.

Requirements::

    - Docker daemon running
    - ``DASHSCOPE_API_KEY`` (and optionally ``DASHSCOPE_MODEL``)
      environment variables set

Run from repo root::

    DASHSCOPE_API_KEY=sk-xxx python docker/builtin_workspace/test/test_agent_e2e.py
"""

import asyncio
import os
import textwrap

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.mcp import MCPClient, HttpMCPConfig
from agentscope.message import UserMsg, ToolCallBlock
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from agentscope.workspace import DockerWorkspace, MCPServerConfig

PYTHON_IMAGE = "agentscope/builtin_workspace:latest"

BUILTIN_MCP_CONFIG = MCPServerConfig(
    name="builtin_tools",
    protocol="stdio",
    command="python",
    args=["/agentscope/builtin_mcp_server.py"],
)


multiply_script = textwrap.dedent("""\
    from mcp.server import FastMCP

    def multiply(a: float, b: float) -> float:
        return a * b

    def add(a: float, b: float) -> float:
        return a + b

    mcp = FastMCP("Math Server")
    mcp.tool(description="Multiply two number.")(multiply)
    mcp.tool(description="Add two number.")(add)

    if __name__ == "__main__":
        mcp.run(transport="stdio")
""")

def _print_tool_calls(agent: Agent) -> None:
    """Print tool calls from the agent's context history."""
    for msg in agent.state.context:
        if isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolCallBlock):
                    print(f"  [ToolCall] {block.name}({block.input})")


async def test_agent_e2e() -> None:
    """Agent uses Bash/Write/Read tools inside DockerWorkspace to complete
    a task."""
    # --- 1. Create workspace with builtin MCP ---
    ws = DockerWorkspace(
        image=PYTHON_IMAGE,
        startup_commands=[
            "cat > /workspace/math_server.py << 'PYEOF'\n" + multiply_script + "PYEOF",
        ],
        mcp_servers=[
            BUILTIN_MCP_CONFIG,
            MCPServerConfig(
                name="math",
                protocol="stdio",
                command="python",
                args=["/workspace/math_server.py"],
                ),
            ],
        )

    await ws.initialize()
    try:
        # --- 2. Connect MCPClient to the in-container gateway ---
        # The gateway aggregates all MCP servers (builtin_tools + math) into
        # a single Streamable-HTTP endpoint, so one MCPClient sees all tools.

        # TODO: to be replaced after agent api update
        mcp_client = MCPClient(
            name="ws_docker",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                url=f"{ws._gateway_base_url}/mcp",
                headers={"Authorization": f"Bearer {ws._gateway_token}"},
                verify=False,
            ),
        )
        await mcp_client.connect()

        # --- 3. Create model, toolkit, agent ---
        api_key = os.environ["DASHSCOPE_API_KEY"]
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model_name = os.environ.get("DASHSCOPE_MODEL", "qwen-plus")

        credential = OpenAICredential(
            api_key=api_key,
            base_url=base_url,
        )
        model = OpenAIChatModel(
            credential=credential,
            model=model_name,
            stream=False,
        )

        toolkit = Toolkit()
        await toolkit.register_mcp(mcp_client)

        agent = Agent(
            name="docker_agent",
            system_prompt=(
                "You are a helpful assistant with access to tools inside a "
                "Docker container. Use the tools to complete tasks. When "
                "writing a file, use the Write tool with an absolute path "
                "under /workspace/. When reading, use the Read tool. When "
                "running commands, use the Bash tool."
            ),
            model=model,
            toolkit=toolkit,
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.BYPASS,
                ),
            ),
        )

        # --- 4. Send a task that requires tool use ---
        msg = UserMsg(
            name="user",
            content=(
                "Please do the following: "
                "1. Use the Bash tool to run 'echo e2e-test-success'. "
                "2. Use the Write tool to write 'hello from agent' to "
                "/workspace/agent_test.txt. "
                "3. Use the Read tool to read /workspace/agent_test.txt. "
                "Tell me the output of step 1 and the content from step 3."
            ),
        )
        reply = await agent.reply(msg)

        # --- 5. Assert the agent completed the task ---
        reply_text = ""
        if isinstance(reply.content, str):
            reply_text = reply.content
        elif isinstance(reply.content, list):
            reply_text = " ".join(
                b.text for b in reply.content if hasattr(b, "text")
            )

        print("========= ROUND 1 ==========")
        print(reply_text)

        msg = UserMsg(
            name="user",
            content=(
                "Please calculate this: "
                "3.14159 * 3.14159 + 1131 * 787\n"
                "You MUST call multiply & add to finish this!"
            ),
        )
        reply = await agent.reply(msg)
        reply_text = ""
        if isinstance(reply.content, str):
            reply_text = reply.content
        elif isinstance(reply.content, list):
            reply_text = " ".join(
                b.text for b in reply.content if hasattr(b, "text")
            )
        print("========= ROUND 2 ==========")
        print(reply_text)

        print("====== Tool called ======")
        _print_tool_calls(agent)

        await mcp_client.close()
    finally:
        await ws.close()


async def main() -> None:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("Skipping: DASHSCOPE_API_KEY not set.")
        print("=== SKIPPED ===")
        return

    await test_agent_e2e()
    print("\n=== AGENT E2E TEST PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
