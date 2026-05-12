#!/usr/bin/env bash
# Entrypoint for the codebox sandbox container.
#
# Prepares the workspace and starts the StreamableHTTP MCP server.
# Two tools are available: run_ipython_cell, run_shell_command.
#
# Usage: entrypoint.sh [PORT]   (default: 8766)

set -euo pipefail

PORT="${1:-8766}"

mkdir -p /workspace

exec python3 /agentscope_runtime/codebox_mcp_server.py --port "$PORT"
