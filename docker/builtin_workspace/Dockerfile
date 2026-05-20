FROM python:3.11-slim

# System deps: find (list_skills), mkdir/sh (built-in via busybox),
# ripgrep + git (same as builtin_box for consistency)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git && \
    rm -rf /var/lib/apt/lists/*

# Pre-install Python packages required by DockerWorkspace and MCP gateway.
# uvicorn/starlette are still needed by the in-container gateway (_server.py).
# No HTTP server deps needed for builtin_mcp_server.py (stdio-only).
RUN pip install --no-cache-dir \
    mcp \
    uvicorn \
    starlette \
    python-frontmatter \
    httpx

# Copy agentscope source and install dev version
COPY pyproject.toml /build/pyproject.toml
COPY src/ /build/src/
RUN pip install --no-cache-dir /build/ && \
    rm -rf /build

# Copy builtin MCP server (stdio-only, spawned by gateway)
COPY docker/builtin_workspace/builtin_mcp_server.py /agentscope/builtin_mcp_server.py
# Create workspace directory (matches DockerWorkspace.DEFAULT_WORKING_DIR)
RUN mkdir -p /workspace
WORKDIR /workspace

CMD ["sleep", "infinity"]
