#!/usr/bin/env bash
# Entrypoint for the builtin-box sandbox container.
#
# Prepares the workspace and starts the StreamableHTTP MCP wrapper.
# All builtin tools are aggregated behind http://0.0.0.0:<PORT>/mcp.
#
# Usage: entrypoint.sh [PORT]   (default: 8765)

set -euo pipefail

PORT="${1:-8765}"

mkdir -p /workspace

exec python3 /agentscope_runtime/builtin_mcp_wrapper.py --port "$PORT"
