import os
import asyncio
import socket
import subprocess
import threading

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock
from agentscope.mcp import HttpStatelessClient
from agentscope_runtime.sandbox import BaseSandboxAsync


# ---------------------------------------------------------------------------
# Host Tool Server: routes tool calls from code_sandbox back to host,
# then dispatches to MCP client.
# ---------------------------------------------------------------------------

class CallToolRequest(BaseModel):
    arguments: dict = {}


class HostToolServer:
    """HTTP server on host that code_sandbox proxies call back to.

    Routes tool calls to the corresponding MCP client.
    """

    def __init__(self):
        self.app = FastAPI()
        self._mcp_tools: dict[str, dict] = {}  # name -> json_schema
        self._port: int | None = None
        self._toolname_client: dict[str, HttpStatelessClient] = {}
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        @self.app.post("/call/{tool_name}")
        async def call_tool(tool_name: str, body: CallToolRequest):
            print(f'\033[33m我是host proxy，我正在调用：{tool_name}\033[0m')
            # Try MCP tools
            if tool_name in self._toolname_client:
                try:
                    func = await self._toolname_client[tool_name].get_callable_function(
                        tool_name, wrap_tool_result=False,
                    )
                    result = await func(**body.arguments)

                    # The 'result' is a CallToolResult object. 
                    # Use .model_dump() to convert the entire Pydantic object to a dict instantly.
                    # This captures content, isError, and meta automatically.
                    return result.structuredContent
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}

            return {"error": f"Tool '{tool_name}' not found"}

    def register_mcp_tool(self, name: str, json_schema: dict):
        """Register an MCP tool (will be routed via HttpStatelessClient)."""
        self._mcp_tools[name] = json_schema

    async def register_mcp_client_tools(self, mcp_client):
        """Auto-discover and register all tools from the MCP client."""
        if not mcp_client:
            return
        mcp_tools = await mcp_client.list_tools()
        for tool in mcp_tools:
            print(f"\033[33mREGISTER ToolServer\033[0m: {tool.name}")
            self._toolname_client[tool.name] = mcp_client
            self.register_mcp_tool(tool.name, {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema if tool.inputSchema else {},
                    "outputSchema": tool.outputSchema if tool.outputSchema else {}
                },
            })

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 0))
        self._port = sock.getsockname()[1]
        sock.close()

        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.should_exit = True

    @property
    def port(self) -> int:
        return self._port


# ---------------------------------------------------------------------------
# Proxy code generation: inject into code_sandbox so it can call host tools
# ---------------------------------------------------------------------------

def _generate_mcp_proxy_code(tool_name: str, tool_schema: dict, host_tool_url: str) -> str:
    """Generate sandbox-side proxy code for an MCP tool (routed via host)."""
    func = tool_schema["function"]
    params = list(func.get("parameters", {}).get("properties", {}).keys())
    params_str = ", ".join(params)
    call_args_str = ", ".join(f'"{p}": {p}' for p in params)
    endpoint = f"{host_tool_url}/call/{tool_name}"

    lines = [
        f"def {tool_name}({params_str}):",
        f'    """{func.get("description", "")}"""',
        "    import requests as _req",
        "    _resp = _req.post(",
        f'        "{endpoint}",',
        "        json={",
        '            "arguments": {',
        call_args_str,
        "            },",
        "        },",
        "        timeout=30,",
        "    )",
        "    _resp.raise_for_status()",
        "    return _resp.json()",
    ]
    return "\n".join(lines)


def _get_docker_bridge_ip() -> str:
    """Auto-detect the Docker bridge gateway IP."""
    try:
        result = subprocess.run(
            ["docker", "network", "inspect", "bridge",
             "--format", "{{range .IPAM.Config}}{{.Gateway}}{{end}}"],
            capture_output=True, text=True, timeout=5,
        )
        ip = result.stdout.strip()
        if ip:
            return ip
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ip", "route"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "docker0" in line:
                return line.split()[8]
    except Exception:
        pass
    return "172.17.0.1"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # 1. Create HttpStatelessClient for MCP tools (e.g. get_news, get_weather)
    mcp_sandbox_url = "http://localhost:49152"
    mcp_client = HttpStatelessClient(
        name="mcp-sandbox",
        transport="streamable_http",
        url=f"{mcp_sandbox_url}/mcp",
    )

    # 2. Start host tool server — routes calls to local functions or mcp_client
    tool_server = HostToolServer()

    # Register MCP tools on host server
    await tool_server.register_mcp_client_tools(mcp_client=mcp_client)

    tool_server.start()
    host_ip = _get_docker_bridge_ip()
    host_tool_url = f"http://{host_ip}:{tool_server.port}"

    # 3. Start BaseSandboxAsync for code execution
    code_sandbox = BaseSandboxAsync()
    await code_sandbox.__aenter__()

    # 4. Inject proxy functions into code_sandbox — both host tools and MCP tools
    for tool_name, schema in tool_server._mcp_tools.items():
        print(f'\033[33mInjection into codebox: {tool_name}\033[0m')
        proxy_code = _generate_mcp_proxy_code(tool_name, schema, host_tool_url)
        await code_sandbox.run_ipython_cell(code=proxy_code)


    try:
        toolkit = Toolkit()

        # --- Register code execution tools (from BaseSandboxAsync) ---
        async def execute_python_code(code: str) -> ToolResponse:
            """Execute the given python code in a sandbox and capture the
            output. Note you must use `print` to see the result.

            Args:
                code (`str`): The Python code to be executed.

            Returns:
                `ToolResponse`: The response containing the execution output.
            """
            try:
                result = await code_sandbox.run_ipython_cell(code=code)
                parts = []
                for item in result.get("content", []):
                    if item.get("type") == "text":
                        desc = item.get("description", "")
                        text = item.get("text", "")
                        if desc == "stdout" and text:
                            parts.append(text)
                        elif desc == "stderr":
                            parts.append(f"<stderr>{text}</stderr>")
                output = "\n".join(parts) if parts else str(result)
                return ToolResponse(
                    content=[TextBlock(type="text", text=output)],
                )
            except Exception as e:
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"Error: {e}")],
                )

        async def execute_shell_command(command: str) -> ToolResponse:
            """Execute the given shell command in a sandbox and capture the
            output.

            Args:
                command (`str`): The shell command to be executed.

            Returns:
                `ToolResponse`: The response containing the command output.
            """
            try:
                result = await code_sandbox.run_shell_command(command=command)
                parts = []
                for item in result.get("content", []):
                    if item.get("type") == "text":
                        desc = item.get("description", "")
                        text = item.get("text", "")
                        if desc == "stdout" and text:
                            parts.append(text)
                        elif desc == "stderr":
                            parts.append(f"<stderr>{text}</stderr>")
                        elif desc == "returncode":
                            parts.append(f"<returncode>{text}</returncode>")
                output = "\n".join(parts) if parts else str(result)
                return ToolResponse(
                    content=[TextBlock(type="text", text=output)],
                )
            except Exception as e:
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"Error: {e}")],
                )

        desc = '\n'.join(f"{name}: {schema}" for name, schema in tool_server._mcp_tools.items())
        toolkit.register_tool_function(
            execute_python_code,
            func_description="Tool to execute python code.\n "
            "Tools can be used in the python code:\n"+ desc)
        toolkit.register_tool_function(execute_shell_command)

        # # --- Register MCP tools (from HttpStatelessClient) ---
        # await toolkit.register_mcp_client(
        #     mcp_client,
        # )


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
        await code_sandbox.__aexit__(None, None, None)
        tool_server.stop()


asyncio.run(main())
