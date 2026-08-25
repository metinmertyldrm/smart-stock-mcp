"""Tests for MCP tool discovery, routing, and request-scoped RBAC."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from identity import ROLE_OPERATOR, ROLE_VIEWER  # noqa: E402
from mcp_client import MCPClient  # noqa: E402
from rbac import AuthorizationError, reset_current_role, set_current_role  # noqa: E402


class FakeSession:
    def __init__(self, *tool_names):
        self.tool_names = tool_names
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name) for name in self.tool_names]
        )

    async def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments or {}))
        return {"success": True}


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

    async def test_draft_management_tools_are_never_advertised_to_model(self):
        client = MCPClient({})
        client.sessions = {
            "marketplace-server": FakeSession(
                "list_marketplace_orders",
                "reject_purchase_draft",
                "delete_purchase_draft",
            )
        }

        tools = await client.list_tools()

        self.assertEqual([tool.name for tool in tools], ["list_marketplace_orders"])

    async def test_viewer_catalog_hides_all_write_tools(self):
        client = MCPClient({})
        client.sessions = {
            "stock-server": FakeSession(
                "list_out_of_stock",
                "create_purchase_draft",
                "place_order",
                "receive_orders",
            )
        }
        token = set_current_role(ROLE_VIEWER)
        try:
            tools = await client.list_tools()
        finally:
            reset_current_role(token)
        self.assertEqual([tool.name for tool in tools], ["list_out_of_stock"])

    async def test_operator_catalog_exposes_draft_but_not_confirm_tools(self):
        client = MCPClient({})
        client.sessions = {
            "stock-server": FakeSession(
                "list_out_of_stock",
                "create_purchase_draft",
                "place_order",
                "receive_orders",
            )
        }
        token = set_current_role(ROLE_OPERATOR)
        try:
            tools = await client.list_tools()
        finally:
            reset_current_role(token)
        self.assertEqual(
            [tool.name for tool in tools],
            ["list_out_of_stock", "create_purchase_draft"],
        )

    async def test_dispatch_is_blocked_before_forbidden_tool_reaches_session(self):
        client = MCPClient({})
        session = FakeSession("place_order")
        client.sessions = {"marketplace-server": session}
        client.tool_to_server = {"place_order": "marketplace-server"}
        token = set_current_role(ROLE_OPERATOR)
        try:
            with self.assertRaises(AuthorizationError):
                await client.call_tool("place_order", {"draft_id": 1})
        finally:
            reset_current_role(token)
        self.assertEqual(session.calls, [])

    async def test_operator_draft_dispatch_reaches_session(self):
        client = MCPClient({})
        session = FakeSession("create_purchase_draft")
        client.sessions = {"marketplace-server": session}
        client.tool_to_server = {"create_purchase_draft": "marketplace-server"}
        token = set_current_role(ROLE_OPERATOR)
        try:
            result = await client.call_tool("create_purchase_draft", {"items": []})
        finally:
            reset_current_role(token)
        self.assertEqual(result, {"success": True})
        self.assertEqual(session.calls, [("create_purchase_draft", {"items": []})])


if __name__ == "__main__":
    unittest.main()
