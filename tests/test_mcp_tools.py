"""
Unit tests for the Model Context Protocol (MCP) tool adapter and client.
Tests JSON-RPC stdio protocol exchange, tool registration, and graceful fallback.
"""
import unittest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from core.tools.mcp_tools import (
    MCPStdioClient,
    MCPToolDefinition,
    create_mcp_tool_adapter
)


class TestMCPTools(unittest.TestCase):

    def test_mcp_tool_definition_dataclass(self):
        tool_def = MCPToolDefinition(
            name="web_search",
            description="Search the live web",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}}
        )
        self.assertEqual(tool_def.name, "web_search")
        self.assertIn("query", tool_def.input_schema["properties"])

    def test_mcp_client_rpc_handshake_and_dispatch(self):
        client = MCPStdioClient(command="dummy_mcp_server")
        
        # Mock the underlying subprocess and streams
        mock_proc = MagicMock()
        mock_stdin = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdout = MagicMock()
        
        # Prepare mock responses for initialize, tools/list, and tools/call
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "MockServer"}}}) + "\n"
        list_resp = json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [{"name": "echo", "description": "Echo back", "inputSchema": {}}]}
        }) + "\n"
        call_resp = json.dumps({
            "jsonrpc": "2.0", "id": 3,
            "result": {"content": [{"type": "text", "text": "Hello MCP"}]}
        }) + "\n"

        mock_stdout.readline = AsyncMock(side_effect=[
            init_resp.encode("utf-8"),
            list_resp.encode("utf-8"),
            call_resp.encode("utf-8")
        ])

        mock_proc.stdin = mock_stdin
        mock_proc.stdout = mock_stdout
        mock_proc.returncode = None
        client.process = mock_proc

        async def _test():
            # 1. Initialize
            init_res = await client.initialize()
            self.assertEqual(init_res["serverInfo"]["name"], "MockServer")

            # 2. List tools
            tools = await client.list_tools()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].name, "echo")

            # 3. Call tool
            call_res = await client.call_tool("echo", {"msg": "Hello"})
            self.assertEqual(call_res["content"][0]["text"], "Hello MCP")

        asyncio.run(_test())

    def test_mcp_adapter_success_and_fallback_resilience(self):
        client = MagicMock(spec=MCPStdioClient)
        
        # Case 1: Successful execution
        client.call_tool = AsyncMock(return_value={"result": "Remote Search Success"})
        adapter = create_mcp_tool_adapter(client=client, tool_name="remote_search")

        async def _run_success():
            # Note: Agents SDK function_tool wraps the coroutine
            coro = getattr(adapter, "__wrapped__", adapter)
            res = await coro(query="test query")
            self.assertTrue(res["success"])
            self.assertEqual(res["source"], "mcp")
            self.assertEqual(res["data"]["result"], "Remote Search Success")

        asyncio.run(_run_success())

        # Case 2: MCP Failure triggers local fallback
        client.call_tool = AsyncMock(side_effect=ConnectionResetError("MCP server crashed"))
        fallback_mock = AsyncMock(return_value={"local_result": "Local Engine Success"})
        adapter_with_fb = create_mcp_tool_adapter(
            client=client,
            tool_name="remote_search",
            fallback_tool=fallback_mock
        )

        async def _run_fallback():
            coro = getattr(adapter_with_fb, "__wrapped__", adapter_with_fb)
            res = await coro(query="test query")
            self.assertTrue(res["success"])
            self.assertEqual(res["source"], "local_fallback")
            self.assertEqual(res["data"]["local_result"], "Local Engine Success")
            fallback_mock.assert_called_once_with(query="test query")

        asyncio.run(_run_fallback())

        # Case 3: Both MCP and Fallback fail -> Graceful structured error (no unhandled crash)
        failing_fallback = AsyncMock(side_effect=RuntimeError("Fallback disk error"))
        adapter_total_fail = create_mcp_tool_adapter(
            client=client,
            tool_name="remote_search",
            fallback_tool=failing_fallback
        )

        async def _run_total_fail():
            coro = getattr(adapter_total_fail, "__wrapped__", adapter_total_fail)
            res = await coro(query="test query")
            self.assertFalse(res["success"])
            self.assertIn("failed", res["error"])

        asyncio.run(_run_total_fail())

    def test_workflow_mcp_integration_wiring(self):
        """Verify that ResearchWorkflow properly accepts and wires mcp_client."""
        from core.workflow import ResearchWorkflow
        from core.context import RunContext

        mock_mcp = MagicMock(spec=MCPStdioClient)
        ctx = RunContext(topic="MCP Integration Test")
        workflow = ResearchWorkflow(context=ctx, mcp_client=mock_mcp)
        self.assertIsNotNone(workflow.mcp_client)
        self.assertEqual(workflow.mcp_client, mock_mcp)


if __name__ == "__main__":
    unittest.main()
