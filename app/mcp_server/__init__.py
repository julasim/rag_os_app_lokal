"""MCP-Server: exponiert das RAG als Tools für MCP-Clients (Claude Desktop, eigene Agents, …)."""
from mcp_server.server import build_mcp_app, MCPAuthMiddleware
from mcp_server import audit, ratelimit

__all__ = ["build_mcp_app", "MCPAuthMiddleware", "audit", "ratelimit"]
