import inspect
import subprocess
from typing import Type

from pydantic import BaseModel

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock
from agentscope_runtime.sandbox import BaseSandboxAsync
from tool_server import ToolServer
from instructions import build_run_python_code_description, _build_registered_tools_str


def _generate_call_tool_code(host_tool_url: str) -> str:
    """Generate a generic call_tool function for the sandbox."""
    endpoint = f"{host_tool_url}/call/"
    return (
        "def call_tool(tool_name, **kwargs):\n"
        '    """Call a tool on the host tool server by name.\n'
        "    Args:\n"
        "        tool_name (str): The name of the tool to call.\n"
        "        **kwargs: Keyword arguments for the tool.\n"
        "    Returns:\n"
        "        dict: The tool result metadata if present, otherwise the\n"
        "        full response dict.\n"
        '    """\n'
        "    import requests as _req\n"
        "    _resp = _req.post(\n"
        f'        "{endpoint}" + tool_name,\n'
        '        json={"arguments": kwargs},\n'
        "        timeout=30,\n"
        "    )\n"
        "    _resp.raise_for_status()\n"
        "    _data = _resp.json()\n"
        "    if 'metadata' in _data and 'isError' not in _data:\n"
        "        return _data['metadata']\n"
        "    return _data\n"
    )

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
    except Exception as e:
        raise RuntimeError(f"Failed to detect Docker bridge gateway IP: {e}") from e

    raise RuntimeError("Failed to detect Docker bridge gateway IP: no IP found")



class CodeActEnv:
    """Manages a sandbox (BaseSandboxAsync) + host tool server (ToolServer).

    - Starts a Docker sandbox for code execution.
    - Starts a ToolServer on the host that exposes local tool functions via HTTP.
    - Injects a ``call_tool`` proxy into the sandbox so that code running
      inside the container can call tools transparently.
    """

    def __init__(self):
        self.sandbox: BaseSandboxAsync | None = None
        self.tool_server: ToolServer = ToolServer()
        self._callable_tools: dict[str, callable] = {}
        self._output_models: dict[str, Type[BaseModel] | None] = {}
        self._started: bool = False

    # ------------------------------------------------------------------
    # Tool registration (call before start)
    # ------------------------------------------------------------------

    def register_callable_tool(
        self,
        func: callable,
        output_model: Type[BaseModel] | None = None,
    ):
        """Register a local function, will be added to run_python_code's docstring."""
        self._callable_tools[func.__name__] = func
        self._output_models[func.__name__] = output_model
        self.tool_server.register(func)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the ToolServer, the sandbox, and inject proxy functions."""
        if self._started:
            return

        # 2. Start host tool server
        self.tool_server.start()
        host_ip = _get_docker_bridge_ip()
        host_tool_url = f"http://{host_ip}:{self.tool_server.port}"

        # 3. Start sandbox
        self.sandbox = BaseSandboxAsync()
        await self.sandbox.__aenter__()

        # 4. Inject generic call_tool into sandbox
        call_tool_code = _generate_call_tool_code(host_tool_url)
        await self.sandbox.run_ipython_cell(code=call_tool_code)

        self._started = True

    async def stop(self):
        """Stop the sandbox and the tool server."""
        if not self._started:
            return
        if self.sandbox:
            await self.sandbox.__aexit__(None, None, None)
            self.sandbox = None
        self.tool_server.stop()
        self._started = False

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------



    async def run_python_code(self, code: str) -> ToolResponse:
        """Execute Python code in the sandbox and return the output."""
        if not self.sandbox:
            return ToolResponse(
                content=[TextBlock(type="text", text="Error: sandbox not started")],
            )
        try:
            result = await self.sandbox.run_ipython_cell(code=code)
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

    @property
    def run_python_code_description(self):
        return build_run_python_code_description(self._callable_tools, self._output_models)
