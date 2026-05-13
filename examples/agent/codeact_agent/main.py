import os
import asyncio
from typing import Sequence

from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit
from codebox import CodeActEnv
from get_weather import get_weather, WeatherOutput
from get_news import get_news, NewsOutput


def build_codeact_instructions() -> str:
    """Build dynamic CodeAct instructions for the effective sandbox state."""
    usage_note = (
        "Some tools may also appear directly, but prefer `run_python_code` whenever you need to combine Python "
        "control flow with sandbox tool calls."
    )

    output_note = (
        "To surface results from `run_python_code`, end the code with `print(...)`; the sandbox does not "
        "return the value of the last expression."
    )

    return f"""You have one primary tool: run_python_code.

Prefer one run_python_code call per request when possible.
Its tool description contains the current `call_tool(...)` guidance.

{output_note}

{usage_note}
"""


async def main():
    codebox = CodeActEnv()

    # Register tools callable in the codebox
    codebox.register_callable_tool(get_weather, output_model=WeatherOutput)
    codebox.register_callable_tool(get_news, output_model=NewsOutput)

    # Start sandbox + tool server + inject proxies
    await codebox.start()

    try:
        toolkit = Toolkit()

        # Register code execution tools (from sandbox)
        toolkit.register_tool_function(
            codebox.run_python_code,
            func_description=codebox.description,
        )

        # Register host tools directly (agent can call them without sandbox)
        toolkit.register_tool_function(get_weather)
        toolkit.register_tool_function(get_news)

        agent = ReActAgent(
            name="Friday",
            sys_prompt=build_codeact_instructions(),
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
        await codebox.stop()


asyncio.run(main())
