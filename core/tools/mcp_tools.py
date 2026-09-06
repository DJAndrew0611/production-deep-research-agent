"""
Model Context Protocol (MCP) Stdio Client & FunctionTool Adapter.
Enables research agents to discover and invoke tools hosted on MCP servers
with automatic resilience and local tool fallback.
"""
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from agents.tool import function_tool

logger = logging.getLogger("deep_research.mcp")


@dataclass
class MCPToolDefinition:
    """Metadata describing an MCP tool discovered via tools/list."""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)


class MCPStdioClient:
    """
    Lightweight, asynchronous Model Context Protocol (MCP) client communicating over stdio.
    Implements JSON-RPC 2.0 spec for 'initialize', 'tools/list', and 'tools/call'.
    """

    def __init__(self, command: str, args: Optional[List[str]] = None, timeout: float = 15.0):
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._is_initialized = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def start(self):
        """Start the MCP server subprocess."""
        if self.process is not None and self.process.returncode is None:
            return

        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info(f"Started MCP server subprocess: {self.command} {' '.join(self.args)}")
        except Exception as exc:
            logger.error(f"Failed to spawn MCP server process: {exc}")
            raise

    async def stop(self):
        """Terminate the MCP server subprocess cleanly."""
        if self.process is not None:
            try:
                if self.process.returncode is None:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                if self.process.returncode is None:
                    self.process.kill()
            finally:
                self.process = None
                self._is_initialized = False

    async def _send_rpc_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a single JSON-RPC 2.0 request over stdin and await response on stdout."""
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP process is not running or stdio streams are unavailable.")

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }
        msg = json.dumps(payload) + "\n"

        self.process.stdin.write(msg.encode("utf-8"))
        await self.process.stdin.drain()

        # Read line from stdout with timeout
        line_bytes = await asyncio.wait_for(self.process.stdout.readline(), timeout=self.timeout)
        if not line_bytes:
            raise EOFError("MCP server process closed stdout unexpectedly.")

        resp = json.loads(line_bytes.decode("utf-8").strip())
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"MCP RPC Error [{err.get('code')}]: {err.get('message')}")

        return resp.get("result", {})

    async def initialize(self, client_name: str = "DeepResearchAgent", client_version: str = "3.0") -> Dict[str, Any]:
        """Send MCP 'initialize' handshake."""
        await self.start()
        result = await self._send_rpc_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": client_name, "version": client_version}
        })
        self._is_initialized = True
        return result

    async def list_tools(self) -> List[MCPToolDefinition]:
        """Query MCP server for available tools via 'tools/list'."""
        if not self._is_initialized:
            await self.initialize()

        result = await self._send_rpc_request("tools/list")
        tools_data = result.get("tools", [])
        return [
            MCPToolDefinition(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {})
            )
            for t in tools_data
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke an MCP tool via 'tools/call'."""
        if not self._is_initialized:
            await self.initialize()

        result = await self._send_rpc_request("tools/call", {
            "name": name,
            "arguments": arguments
        })
        return result


def create_mcp_tool_adapter(
    client: MCPStdioClient,
    tool_name: str,
    description: str = "MCP delegated tool",
    fallback_tool: Optional[Callable[..., Awaitable[Any]]] = None
):
    """
    Factory converting a remote MCP tool into an OpenAI Agents SDK @function_tool.
    Provides automatic fallback to a local tool function if the MCP server fails.
    """
    @function_tool(strict_mode=False)
    async def mcp_adapted_tool(**kwargs) -> Dict[str, Any]:
        """Invoke remote MCP tool with resilience and local fallback."""
        try:
            res = await client.call_tool(tool_name, kwargs)
            return {"success": True, "data": res, "source": "mcp"}
        except Exception as exc:
            logger.warning(f"MCP tool '{tool_name}' call failed ({exc}); attempting fallback...")
            if fallback_tool is not None:
                try:
                    fallback_res = await fallback_tool(**kwargs)
                    return {"success": True, "data": fallback_res, "source": "local_fallback"}
                except Exception as fb_exc:
                    logger.error(f"Fallback tool execution also failed: {fb_exc}")
                    return {"success": False, "error": f"MCP and fallback failed: {exc} | {fb_exc}"}
            return {"success": False, "error": f"MCP tool call failed: {exc}"}

    return mcp_adapted_tool
