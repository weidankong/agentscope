import os
import asyncio

from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit
from agentscope.mcp import HttpStatelessClient
from agentscope_runtime.sandbox import McpSandboxAsync


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # 1. Start sandbox (launches Docker container with MCP server)
    sandbox = McpSandboxAsync()
    await sandbox.__aenter__()

    toolkit = Toolkit()

    # Create a tool group for MCP tools from sandbox
    toolkit.create_tool_group(
        group_name="sandbox_tools",
        description="Tools from MCP sandbox.",
    )

    # 2. Connect to the sandbox MCP server via streamable_http
    # Note: sandbox.get_info()['url'] gives the host-mapped URL (dynamic port)
    sandbox_url = "http://localhost:49152"
    print(sandbox_url)

    sandbox_client = HttpStatelessClient(
        name="mcp-sandbox",
        transport="streamable_http",
        url=f"{sandbox_url}/mcp",
    )
    print(sandbox_client)

    # list_tools 返回 List[mcp.types.Tool]
    tools = await sandbox_client.list_tools()
    print(sandbox_client)
    await toolkit.register_mcp_client(
        sandbox_client,
        group_name="sandbox_tools",
    )

    try:
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You're a helpful assistant named Friday.",
            model=DashScopeChatModel(
                model_name="qwen-max",
                api_key=os.environ["DASHSCOPE_API_KEY"],
                stream=True,
            ),
            memory=InMemoryMemory(),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
        )

        user = UserAgent(name="user")

        msg = None
        while True:
            msg = await agent(msg)
            msg = await user(msg)
            if msg.get_text_content() == "exit":
                break
    finally:
        await sandbox.__aexit__(None, None, None)


asyncio.run(main())
