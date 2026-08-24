import unittest

from identity import ROLE_ADMIN, ROLE_MANAGER, ROLE_OPERATOR, ROLE_VIEWER
from rbac import (
    AuthorizationError,
    allowed_write_tools,
    authorize_tool,
    current_role,
    reset_current_role,
    set_current_role,
    tool_visible,
)


class RoleAuthorizationTest(unittest.TestCase):
    def test_viewer_can_read_but_cannot_write(self):
        self.assertTrue(tool_visible("list_out_of_stock", role=ROLE_VIEWER))
        self.assertFalse(tool_visible("create_purchase_draft", role=ROLE_VIEWER))
        self.assertFalse(tool_visible("place_order", role=ROLE_VIEWER))
        with self.assertRaises(AuthorizationError):
            authorize_tool("create_purchase_draft", role=ROLE_VIEWER)

    def test_operator_can_create_draft_but_cannot_confirm(self):
        self.assertEqual(allowed_write_tools(ROLE_OPERATOR), frozenset({"create_purchase_draft"}))
        authorize_tool("create_purchase_draft", role=ROLE_OPERATOR)
        with self.assertRaises(AuthorizationError):
            authorize_tool("place_order", role=ROLE_OPERATOR)
        with self.assertRaises(AuthorizationError):
            authorize_tool("receive_orders", role=ROLE_OPERATOR)

    def test_manager_and_admin_can_dispatch_confirm_writes(self):
        for role in (ROLE_MANAGER, ROLE_ADMIN):
            authorize_tool("create_purchase_draft", role=role)
            authorize_tool("place_order", role=role)
            authorize_tool("receive_orders", role=role)

    def test_request_role_context_is_reset(self):
        self.assertIsNone(current_role())
        token = set_current_role(ROLE_VIEWER)
        try:
            self.assertEqual(current_role(), ROLE_VIEWER)
            with self.assertRaises(AuthorizationError):
                authorize_tool("place_order")
        finally:
            reset_current_role(token)
        self.assertIsNone(current_role())

    def test_trusted_internal_context_preserves_legacy_execution(self):
        self.assertIsNone(current_role())
        self.assertTrue(tool_visible("place_order"))
        authorize_tool("place_order")


if __name__ == "__main__":
    unittest.main()
