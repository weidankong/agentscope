# -*- coding: utf-8 -*-
"""run_ipython_cell tool — execute code in a stateful IPython kernel."""

import asyncio
import io
import sys
from contextlib import redirect_stderr, redirect_stdout

from IPython.core.interactiveshell import InteractiveShell
from mcp.types import CallToolResult, TextContent, Tool as McpToolDef

SPLIT_OUTPUT_MODE = True

# Initialize IPython shell (stateful across calls)
ipy = InteractiveShell.instance()

DEFINITION = McpToolDef(
    name="run_ipython_cell",
    description=(
        "Execute code in a stateful IPython (Jupyter) kernel. "
        "Variables persist across calls."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute in the IPython kernel.",
            },
        },
        "required": ["code"],
    },
)


async def run(code: str) -> CallToolResult:
    """Execute code in the IPython kernel and return the results."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def thread_target() -> None:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            preprocessing_exc_tuple = None
            try:
                transformed_cell = ipy.transform_cell(code)
            except Exception:
                transformed_cell = code
                preprocessing_exc_tuple = sys.exc_info()

            if transformed_cell is None:
                raise RuntimeError(
                    "IPython cell transformation failed: transformed_cell is None.",
                )

            asyncio.run(
                ipy.run_cell_async(
                    code,
                    transformed_cell=transformed_cell,
                    preprocessing_exc_tuple=preprocessing_exc_tuple,
                ),
            )

    await asyncio.to_thread(thread_target)

    stdout_content = stdout_buf.getvalue()
    stderr_content = stderr_buf.getvalue()

    content_list: list[TextContent] = []

    if SPLIT_OUTPUT_MODE:
        content_list.append(
            TextContent(type="text", text=stdout_content, description="stdout"),
        )
        if stderr_content:
            content_list.append(
                TextContent(type="text", text=stderr_content, description="stderr"),
            )
    else:
        content_list.append(
            TextContent(
                type="text",
                text=stdout_content + "\n" + stderr_content,
                description="output",
            ),
        )

    return CallToolResult(
        content=content_list,
        isError=bool(stderr_content),
    )
