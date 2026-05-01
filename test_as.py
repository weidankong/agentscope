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
from agentscope.tool import Toolkit, get_weather, ToolResponse
from agentscope.message import TextBlock
from agentscope_runtime.sandbox import BaseSandboxAsync


# ---------------------------------------------------------------------------
# Host Tool Server: exposes host tool functions via HTTP for sandbox callback
# ---------------------------------------------------------------------------

class CallToolRequest(BaseModel):
    arguments: dict = {}


class HostToolServer:
    def __init__(self):
        self.app = FastAPI()
        self._tools: dict = {}
        self._port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        @self.app.post("/call/{tool_name}")
        async def call_tool(tool_name: str, body: CallToolRequest):
            if tool_name not in self._tools:
                return {"error": f"Tool '{tool_name}' not found"}
            try:
                result = self._tools[tool_name](**body.arguments)
                # If the tool returns ToolResponse, extract text content
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

    def register(self, name: str, func):
        self._tools[name] = func

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


def _generate_proxy_code(func, host_tool_url: str) -> str:
    """Generate sandbox-side proxy code that matches the original function's
    name and signature, but delegates to HTTP callback internally."""
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
    # Fallback: parse ip route
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
    # 1. Start host tool server
    tool_server = HostToolServer()
    tool_server.register("get_weather", get_weather)
    tool_server.start()

    host_ip = _get_docker_bridge_ip()
    host_tool_url = f"http://{host_ip}:{tool_server.port}"

    # 2. Start sandbox
    sandbox = BaseSandboxAsync()
    await sandbox.__aenter__()

    # 3. Inject proxy functions into sandbox — same name/signature as host tools
    for tool_name, tool_func in tool_server._tools.items():
        proxy_code = _generate_proxy_code(tool_func, host_tool_url)
        await sandbox.run_ipython_cell(code=proxy_code)

    try:
        toolkit = Toolkit()

        # Sandbox tools
        async def execute_python_code(code: str) -> ToolResponse:
            """Execute the given python code in a sandbox and capture the
            output. Note you must `print` the output to get the result.
            Host tool functions (e.g. get_weather) are available directly
            in the sandbox namespace.

            Args:
                code (`str`): The Python code to be executed.

            Returns:
                `ToolResponse`: The response containing the execution output.
            """
            try:
                result = await sandbox.run_ipython_cell(code=code)
                # result is CallToolResult.model_dump():
                # {"content": [{"type": "text", "text": "...", "description": "stdout"}], ...}
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
                result = await sandbox.run_shell_command(command=command)
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

        # Host tool: get_weather runs on the host machine
        toolkit.register_tool_function(get_weather)

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
        tool_server.stop()


asyncio.run(main())
