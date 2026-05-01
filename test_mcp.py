import os
import asyncio

from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock
from agentscope_runtime.sandbox import McpSandboxAsync


def _make_sandbox_proxy(sandbox: McpSandboxAsync, tool_name: str):
    """Create a proxy function that calls sandbox.call_tool_async."""
    async def proxy(**kwargs):
        result = await sandbox.call_tool_async(tool_name, kwargs)
        texts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                texts.append(TextBlock(type="text", text=block["text"]))
        return ToolResponse(content=texts)

    return proxy


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # 2. Start sandbox
    sandbox = McpSandboxAsync()
    await sandbox.__aenter__()

    try:
        toolkit = Toolkit()
        mcps = await sandbox.list_mcps_async()
        for server_name, tools in mcps.items():
            for tool_name, tool_info in tools.items():
                schema = tool_info["json_schema"]
                proxy = _make_sandbox_proxy(sandbox, tool_name)
                toolkit.register_tool_function(
                    proxy,
                    func_name=tool_name,
                    json_schema=schema,
                    async_execution=False,
                )

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
