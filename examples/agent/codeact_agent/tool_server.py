import inspect
import socket
import threading

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel


class CallToolRequest(BaseModel):
    arguments: dict = {}


class ToolServer:
    """HTTP server on host that code_sandbox proxies call back to.

    Routes tool calls to registered local functions.
    """

    def __init__(self):
        self.app = FastAPI()
        self._toolname_func: dict[str, callable] = {}
        self._port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        @self.app.post("/call/{tool_name}")
        async def call_tool(tool_name: str, body: CallToolRequest):
            if tool_name in self._toolname_func:
                try:
                    result = self._toolname_func[tool_name](**body.arguments)
                    resp = {
                        "content": [
                            {"type": b.type, "text": b.text}
                            for b in result.content
                            if hasattr(b, "text")
                        ],
                    }
                    if result.metadata is not None:
                        resp["metadata"] = result.metadata
                    return resp
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}

            return {"content": [{"type": "text", "text": f"Error: Tool '{tool_name}' not found"}], "isError": True}

    def register(self, func: callable):
        """Register a tool function that can be called from the sandbox."""
        self._toolname_func[func.__name__] = func

    @staticmethod
    def _build_schema(func: callable) -> dict:
        sig = inspect.signature(func)
        return {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": inspect.getdoc(func) or "",
                "parameters": {
                    "type": "object",
                    "properties": {
                        p.name: {"type": "string"}
                        for p in sig.parameters.values()
                    },
                    "required": [
                        p.name for p in sig.parameters.values()
                        if p.default is inspect.Parameter.empty
                    ],
                },
            },
        }

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
