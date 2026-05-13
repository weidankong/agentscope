import inspect
from typing import Type

from pydantic import BaseModel


CODEACT_SYSTEM_PROMPT = """You have one primary tool: run_python_code.

Prefer one run_python_code call per request when possible.
Its tool description contains the current `call_tool(...)` guidance.

To surface results from `run_python_code`, end the code with `print(...)`; the sandbox does not \
return the value of the last expression.

Some tools may also appear directly, but prefer `run_python_code` whenever you need to combine Python \
control flow with sandbox tool calls.
"""

def _build_registered_tools_str(
    callable_tools: dict[str, callable],
    output_models: dict[str, Type[BaseModel] | None],
) -> str:
    lines = []
    for name, func in callable_tools.items():
        sig = inspect.signature(func)
        params_str = ", ".join(
            f"{p}: {a.annotation.__name__}"
            if a.annotation is not inspect.Parameter.empty
            else p
            for p, a in sig.parameters.items()
        )
        doc_first_line = (inspect.getdoc(func) or "").split("\n")[0]
        entry = f"  - {name}({params_str}): {doc_first_line}"

        output_model = output_models.get(name)
        if output_model is not None:
            schema = output_model.model_json_schema()
            # Strip title and $defs for a concise inline representation
            schema.pop("title", None)
            defs = schema.pop("$defs", None)
            entry += f"\n    Output schema: {schema}"
        lines.append(entry)
    return "\n".join(lines)


def build_run_python_code_description(
    callable_tools: dict[str, callable],
    output_models: dict[str, Type[BaseModel] | None],
) -> str:
    """Build the tool description for run_python_code."""
    registered_tools = _build_registered_tools_str(callable_tools, output_models)
    return (
        "Execute Python code in an IPython sandbox and return the output.\n\n"
        "The code runs as an **IPython cell** — **NEVER** wrap it in main function.\n"
        "Inside the sandbox, `call_tool(name, **kwargs)` is available as a "
        "synchronous built-in for registered host callbacks. Use the tool name as the first "
        "argument and keyword arguments only — do not pass a dict or any other "
        "positional arguments after the tool name.\n\n"

        "Prefer `run_python_code` when you need to combine one or more"
        "`call_tool(...)` calls with Python control flow, loops, or"
        "post-processing.\n\n"

        "Registered sandbox tools:"
        f"    {registered_tools}\n\n"
        "Args:\n"
        "    code (`str`): The Python code to be executed.\n\n"
        "Returns:\n"
        "    `ToolResponse`: The response containing the execution output.\n"
    )
