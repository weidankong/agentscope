# -*- coding: utf-8 -*-
"""run_shell_command tool — execute a shell command."""

import asyncio

from mcp.types import CallToolResult, TextContent, Tool as McpToolDef

SPLIT_OUTPUT_MODE = True

DEFINITION = McpToolDef(
    name="run_shell_command",
    description="Execute a shell command and return stdout, stderr, and exit code.",
    inputSchema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
        },
        "required": ["command"],
    },
)


async def run(command: str) -> CallToolResult:
    """Execute a shell command and return the results."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_bytes, stderr_bytes = await proc.communicate()

    stdout_content = stdout_bytes.decode()
    stderr_content = stderr_bytes.decode()

    content_list: list[TextContent] = []

    if SPLIT_OUTPUT_MODE:
        content_list.append(
            TextContent(type="text", text=stdout_content, description="stdout"),
        )
        if stderr_content:
            content_list.append(
                TextContent(type="text", text=stderr_content, description="stderr"),
            )
        content_list.append(
            TextContent(type="text", text=str(proc.returncode), description="returncode"),
        )
    else:
        content_list.append(
            TextContent(
                type="text",
                text=stdout_content + "\n" + stderr_content + "\n" + str(proc.returncode),
                description="output",
            ),
        )

    return CallToolResult(
        content=content_list,
        isError=bool(stderr_content),
    )
