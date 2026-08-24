import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.responses import StreamingResponse

# secure_api creates its SQLite session store at import time. Keep this focused
# test isolated from the repository working tree.
_SESSION_DB = os.path.join(tempfile.gettempdir(), f"smart-stock-observability-{os.getpid()}.db")
os.environ["LLM_SESSIONS_DB"] = _SESSION_DB
os.environ["LLM_APPROVAL_AUDIT_DB"] = _SESSION_DB
os.environ["LLM_AUTH_MODE"] = "local"

from observability import metrics  # noqa: E402
from rbac import reset_current_role, set_current_role  # noqa: E402
from secure_api import (  # noqa: E402
    app,
    approval_audits,
    approve_purchase_draft,
    build_draft_approval_plan,
    correlate_chat_response,
)


class FakeApprovalClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            SimpleNamespace(name="place_order"),
            SimpleNamespace(name="create_incoming_orders"),
        ]

    async def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments or {}))
        if tool_name == "place_order":
            return {
                "success": True,
                "id": 99,
                "draftId": 42,
                "items": [{"product": {"id": 2}, "quantity": 1}],
                "expectedDeliveryDate": "2099-08-27T10:00:00",
            }
        if tool_name == "create_incoming_orders":
            return {"success": True, "count": 1, "orders": [{"id": 501}]}
        raise AssertionError(f"Unexpected tool: {tool_name}")


def identity(role):
    capabilities = {"read", "draft"}
    if role in {"MANAGER", "ADMIN"}:
        capabilities.add("confirm")
    return SimpleNamespace(
        id=f"{role.casefold()}-1",
        username=role.casefold(),
        role=role,
        has=lambda capability: capability in capabilities,
    )


class SecuredResponseObservabilityTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(_SESSION_DB)
        except FileNotFoundError:
            pass

    def setUp(self):
        metrics.reset()

    def test_streamed_chat_response_is_correlated_and_aggregated(self):
        payload = {
            "conversationId": "conv-1",
            "succeeded": True,
            "plan": {"goal": "INFO"},
            "explanation": {"repaired": False},
            "trace": [
                {"tool": "list_out_of_stock", "status": "success", "durationMs": 12.5}
            ],
            "telemetry": {
                "executionId": "exec-1",
                "durationMs": 50,
                "missingFields": ["HTTP request ID", "tool sürümü"],
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        response = StreamingResponse(iter([raw]), status_code=200, media_type="application/json")

        correlated = asyncio.run(correlate_chat_response(response, "request-12345678901234567890123456789012"))
        restored = json.loads(correlated.body)

        self.assertEqual(
            restored["telemetry"]["requestId"],
            "request-12345678901234567890123456789012",
        )
        self.assertNotIn("HTTP request ID", restored["telemetry"]["missingFields"])
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["chat"]["total"], 1)
        self.assertEqual(snapshot["chat"]["succeeded"], 1)
        self.assertEqual(snapshot["tools"][0]["tool"], "list_out_of_stock")
        self.assertEqual(snapshot["tools"][0]["durationMsAverage"], 12.5)

    def test_pending_draft_response_records_authenticated_creator(self):
        payload = {
            "conversationId": "conv-creator",
            "pendingDraftId": 77,
            "succeeded": True,
            "plan": {"goal": "DRAFT"},
            "trace": [],
            "telemetry": {},
        }
        response = StreamingResponse(
            iter([json.dumps(payload).encode("utf-8")]),
            status_code=200,
            media_type="application/json",
        )

        correlated = asyncio.run(
            correlate_chat_response(response, "request-creator", identity("OPERATOR"))
        )
        restored = json.loads(correlated.body)
        audit = approval_audits.get(77)

        self.assertEqual(restored["telemetry"]["actor"]["role"], "OPERATOR")
        self.assertEqual(audit["createdBy"]["username"], "operator")

    def test_manager_central_approval_executes_fixed_mcp_chain(self):
        client = FakeApprovalClient()
        app.state.agent = SimpleNamespace(client=client)
        request = SimpleNamespace(state=SimpleNamespace(identity=identity("MANAGER")))
        token = set_current_role("MANAGER")
        try:
            result = asyncio.run(approve_purchase_draft(42, request))
        finally:
            reset_current_role(token)

        self.assertTrue(result["success"])
        self.assertEqual(
            [name for name, _ in client.calls],
            ["place_order", "create_incoming_orders"],
        )
        self.assertEqual(
            client.calls[1][1]["items"],
            [{
                "product_id": 2,
                "quantity": 1,
                "expected_delivery_date": "2099-08-27T10:00:00",
            }],
        )
        self.assertEqual(result["audit"]["approvedBy"]["role"], "MANAGER")

    def test_operator_cannot_use_central_approval_endpoint(self):
        client = FakeApprovalClient()
        app.state.agent = SimpleNamespace(client=client)
        request = SimpleNamespace(state=SimpleNamespace(identity=identity("OPERATOR")))

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(approve_purchase_draft(43, request))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(client.calls, [])

    def test_central_approval_plan_has_no_model_selected_tools(self):
        plan = build_draft_approval_plan(42)

        self.assertEqual(plan["goal"], "ORDER")
        self.assertEqual(
            [step["tool"] for step in plan["steps"]],
            ["place_order", "create_incoming_orders"],
        )


if __name__ == "__main__":
    unittest.main()
