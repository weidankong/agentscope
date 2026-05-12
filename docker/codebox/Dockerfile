FROM python:3.12-slim

# Install IPython and system deps
RUN pip install --no-cache-dir ipython && \
    apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir mcp starlette uvicorn ipython

COPY docker/codebox/ /agentscope_runtime/

# Create workspace
RUN mkdir -p /workspace
WORKDIR /workspace

EXPOSE 8766

ENTRYPOINT ["/agentscope_runtime/entrypoint.sh"]
