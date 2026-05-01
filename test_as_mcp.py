import inspect
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
from agentscope_runtime.sandbox import BaseSandboxAsync, McpSandboxAsync


# ---------------------------------------------------------------------------
# Host Tool Server: routes tool calls from code_sandbox back to host,
# then dispatches to either local functions or mcp_sandbox.
# ---------------------------------------------------------------------------

class CallToolRequest(BaseModel):
    arguments: dict = {}


class HostToolServer:
    """HTTP server on host that code_sandbox proxies call back to.

    Supports two kinds of tools:
    - Local host functions (e.g. get_weather)
    - MCP sandbox tools (routed to mcp_sandbox.call_tool_async)
    """

    def __init__(self, mcp_sandbox: McpSandboxAsync | None = None):
        self.app = FastAPI()
        self._host_tools: dict = {}
        self._mcp_tools: dict[str, dict] = {}  # name -> json_schema
        self._mcp_sandbox = mcp_sandbox
        self._port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        @self.app.post("/call/{tool_name}")
        async def call_tool(tool_name: str, body: CallToolRequest):
            print(f'\033[33m我是host proxy，我正在调用：{tool_name}\033[0m')
            # Try MCP sandbox tools first
            if tool_name in self._mcp_tools and self._mcp_sandbox:
                try:
                    result = await self._mcp_sandbox.call_tool_async(
                        tool_name, body.arguments,
                    )
                    texts = []
                    for block in result.get("content", []):
                        if block.get("type") == "text":
                            texts.append(block["text"])
                    return {"result": "\n".join(texts) if texts else str(result)}
                except Exception as e:
                    return {"error": str(e)}

            # Then try local host tools
            if tool_name in self._host_tools:
                try:
                    result = self._host_tools[tool_name](**body.arguments)
                    if isinstance(result, ToolResponse):
                        texts = []
                        for block in result.content:
                            if isinstance(block, dict) and "text" in block:
                                texts.append(block["text"])
                            elif hasattr(block, "text"):
                                texts.append(block.text)
                        return {"result": "\n".join(texts)}
                    return {"result": str(result)}
                except Exception as e:
                    return {"error": str(e)}

            return {"error": f"Tool '{tool_name}' not found"}

    def register_host_tool(self, name: str, func):
        """Register a local host function."""
        self._host_tools[name] = func

    def register_mcp_tool(self, name: str, json_schema: dict):
        """Register an MCP sandbox tool (will be routed to mcp_sandbox)."""
        self._mcp_tools[name] = json_schema

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

def _generate_proxy_code(func, host_tool_url: str) -> str:
    """Generate sandbox-side proxy code for a local host function."""
    sig = inspect.signature(func)
    params = []
    call_args = []
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            params.append(name)
        else:
            params.append(f"{name}={repr(param.default)}")
        call_args.append(f'                "{name}": {name},')

    params_str = ", ".join(params)
    call_args_str = "\n".join(call_args)
    endpoint = f"{host_tool_url}/call/{func.__name__}"

    lines = [
        f"def {func.__name__}({params_str}):",
        f'    """{func.__doc__}"""',
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
        "    _data = _resp.json()",
        '    if "error" in _data:',
        '        raise RuntimeError(_data["error"])',
        '    return _data["result"]',
    ]
    return "\n".join(lines)


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
        "    _data = _resp.json()",
        '    if "error" in _data:',
        '        raise RuntimeError(_data["error"])',
        '    return _data["result"]',
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
# MCP toolkit registration helper
# ---------------------------------------------------------------------------

def _make_mcp_proxy(sandbox: McpSandboxAsync, tool_name: str):
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
    # 1. Start McpSandboxAsync for MCP tools (e.g. get_news)
    mcp_sandbox = McpSandboxAsync()
    await mcp_sandbox.__aenter__()

    # 2. Start host tool server — routes calls to local functions or mcp_sandbox
    tool_server = HostToolServer(mcp_sandbox=mcp_sandbox)

    # Register MCP tools on host server
    mcps = await mcp_sandbox.list_mcps_async()
    for _server_name, tools in mcps.items():
        for tool_name, tool_info in tools.items():
            tool_server.register_mcp_tool(tool_name, tool_info["json_schema"])

    tool_server.start()
    host_ip = _get_docker_bridge_ip()
    host_tool_url = f"http://{host_ip}:{tool_server.port}"

    # 3. Start BaseSandboxAsync for code execution
    code_sandbox = BaseSandboxAsync()
    await code_sandbox.__aenter__()

    # 4. Inject proxy functions into code_sandbox — both host tools and MCP tools
    available_tool_names = []
    for tool_name, tool_func in tool_server._host_tools.items():
        print(f'\033[33minject code into codebox: {tool_name}\033[0m')
        proxy_code = _generate_proxy_code(tool_func, host_tool_url)
        await code_sandbox.run_ipython_cell(code=proxy_code)
        available_tool_names.append(tool_name)

    for tool_name, schema in tool_server._mcp_tools.items():
        print(f'\033[33minject code into codebox: {tool_name}\033[0m')
        proxy_code = _generate_mcp_proxy_code(tool_name, schema, host_tool_url)
        await code_sandbox.run_ipython_cell(code=proxy_code)
        available_tool_names.append(tool_name)

    available_tools_str = ", ".join(available_tool_names)

    try:
        toolkit = Toolkit()

        # --- Register code execution tools (from BaseSandboxAsync) ---
        async def execute_python_code(code: str) -> ToolResponse:
            """Execute the given python code in a sandbox and capture the
            output. Note you must use `print` to see the result.
            The following functions are available in the sandbox namespace:
            """ + available_tools_str + """

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

        toolkit.register_tool_function(execute_python_code)
        toolkit.register_tool_function(execute_shell_command)

        # --- Register MCP tools (from McpSandboxAsync) ---
        for _server_name, tools in mcps.items():
            for tool_name, tool_info in tools.items():
                schema = tool_info["json_schema"]
                proxy = _make_mcp_proxy(mcp_sandbox, tool_name)
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
        await code_sandbox.__aexit__(None, None, None)
        await mcp_sandbox.__aexit__(None, None, None)
        tool_server.stop()


asyncio.run(main())
