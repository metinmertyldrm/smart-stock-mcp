"""Tests for MCP tool discovery and routing."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from mcp_client import MCPClient  # noqa: E402


class FakeSession:
    def __init__(self, *tool_names):
        self.tool_names = tool_names

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name) for name in self.tool_names]
        )


class MCPClientToolListingTests(unittest.IsolatedAsyncioTestCase):
    def test_register_tool_ignores_duplicate_from_same_server(self):
        client = MCPClient({})

        self.assertTrue(client._register_tool("receive_orders", "stock-server"))
        self.assertFalse(client._register_tool("receive_orders", "stock-server"))
        self.assertEqual(client.tool_to_server, {"receive_orders": "stock-server"})

    def test_register_tool_rejects_name_shared_by_different_servers(self):
        client = MCPClient({})
        client._register_tool("receive_orders", "stock-server")

        with self.assertRaisesRegex(ValueError, "marketplace-server"):
            client._register_tool("receive_orders", "marketplace-server")

    async def test_list_tools_removes_duplicate_names(self):
        client = MCPClient({})
        client.sessions = {
            "stock-server": FakeSession("receive_order", "receive_orders", "receive_orders")
        }

        tools = await client.list_tools()

        self.assertEqual([tool.name for tool in tools], ["receive_order", "receive_orders"])


if __name__ == "__main__":
    unittest.main()
