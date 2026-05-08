FROM python:3.12-slim

# Install system deps (ripgrep for Grep, git for common usage)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ripgrep git && \
    rm -rf /var/lib/apt/lists/*

# Copy agentscope source and install dev version
COPY pyproject.toml /build/pyproject.toml
COPY src/ /build/src/
RUN pip install --no-cache-dir /build/ && \
    rm -rf /build

# Copy the StreamableHTTP MCP wrapper and per-tool modules
COPY docker/builtin_box/builtin_mcp_wrapper.py \
     /agentscope_runtime/builtin_mcp_wrapper.py

COPY docker/builtin_box/entrypoint.sh /agentscope_runtime/entrypoint.sh
RUN chmod +x /agentscope_runtime/entrypoint.sh

# Create workspace
RUN mkdir -p /workspace
WORKDIR /workspace

EXPOSE 8765

ENTRYPOINT ["/agentscope_runtime/entrypoint.sh"]
