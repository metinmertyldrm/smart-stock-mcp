import os
import unittest
from unittest.mock import patch

from mcp_client import _server_parameters


class MCPChildEnvironmentTest(unittest.TestCase):
    def test_stock_service_url_is_inherited_by_stdio_child(self):
        expected = "http://stock-service:8081"
        with patch.dict(os.environ, {"STOCK_SERVICE_URL": expected}, clear=False):
            params = _server_parameters("/app/stock-mcp/tools.py")

        self.assertIsNotNone(params.env)
        self.assertEqual(expected, params.env.get("STOCK_SERVICE_URL"))

    def test_child_environment_is_a_copy_of_parent_environment(self):
        with patch.dict(os.environ, {"MCP_ENV_TEST_SENTINEL": "parent"}, clear=False):
            params = _server_parameters("server.py")
            params.env["MCP_ENV_TEST_SENTINEL"] = "child"
            self.assertEqual("parent", os.environ["MCP_ENV_TEST_SENTINEL"])


if __name__ == "__main__":
    unittest.main()
