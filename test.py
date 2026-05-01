import asyncio
from agentscope_runtime.sandbox import McpSandboxAsync


async def main_async():
    async with McpSandboxAsync() as box:
        print('=======TOOLs===========')
        print(await box.list_tools_async())
        print('=======MCPs============')
        print(await box.list_mcps_async())
        print('=======call======')
        print(await box.call_tool_async("get_weather", {"city": "北京"}))
        input("按 Enter 键退出...")


asyncio.run(main_async())
