"""Regression coverage for extracted conversation state and CLI persistence."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from test_support import install_optional_stubs

install_optional_stubs()

import agent_runtime  # noqa: E402
import app  # noqa: E402
import conversation_state  # noqa: E402


class ConversationStateExtractionTest(unittest.TestCase):
    def test_app_reexports_state_api(self):
        self.assertIs(app.ConversationState, conversation_state.ConversationState)
        self.assertIs(app.CachedProcurementPlan, conversation_state.CachedProcurementPlan)
        self.assertIs(app.is_plan_valid, conversation_state.is_plan_valid)
        self.assertIs(app.save_state, conversation_state.save_state)
        self.assertIs(app.load_state, conversation_state.load_state)
        self.assertIs(app.serialize_plan, conversation_state.serialize_plan)

    def test_legacy_runtime_globals_are_rebound(self):
        self.assertIs(agent_runtime.ConversationState, conversation_state.ConversationState)
        self.assertIs(agent_runtime.CachedProcurementPlan, conversation_state.CachedProcurementPlan)
        self.assertIs(agent_runtime.is_plan_valid, conversation_state.is_plan_valid)
        self.assertIs(agent_runtime.save_state, conversation_state.save_state)
        self.assertIs(agent_runtime.load_state, conversation_state.load_state)
        self.assertIs(agent_runtime.serialize_plan, conversation_state.serialize_plan)

    def test_legacy_json_state_round_trip_is_preserved(self):
        state = conversation_state.ConversationState()
        state.last_plan = {"success": True, "items": [1]}
        state.last_cheapest_plan = conversation_state.CachedProcurementPlan(
            objective="CHEAPEST",
            items=[{"product_id": 2, "quantity": 3}],
            result={"success": True, "overall_total": 42.5},
            saved_at=datetime.now(timezone.utc).isoformat(),
        )
        state.last_product = {"id": 2, "name": "Galaxy S24"}
        state.last_replenishment = {"product_id": 2, "replenishment_quantity_needed": 3}
        state.references = {"ref_test": {"type": "product_list", "data": [{"id": 2}]}}
        state.last_reference_id = "ref_test"
        state.last_user_message = "Galaxy S24 stok durumu"
        state.pending_draft_id = 17
        state.history = [{"role": "user", "content": "merhaba"}]

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            conversation_state.save_state(state, path)
            loaded = conversation_state.load_state(path)

        self.assertEqual(loaded.last_plan, state.last_plan)
        self.assertIsInstance(loaded.last_cheapest_plan, conversation_state.CachedProcurementPlan)
        self.assertEqual(loaded.last_cheapest_plan.objective, "CHEAPEST")
        self.assertEqual(loaded.last_product, state.last_product)
        self.assertEqual(loaded.last_replenishment, state.last_replenishment)
        self.assertEqual(loaded.references, state.references)
        self.assertEqual(loaded.last_reference_id, "ref_test")
        self.assertEqual(loaded.pending_draft_id, 17)
        self.assertEqual(loaded.history, state.history)

    def test_cache_validity_keeps_ten_minute_window(self):
        fresh = conversation_state.CachedProcurementPlan(
            objective="CHEAPEST",
            items=[],
            result={"success": True},
            saved_at=datetime.now(timezone.utc).isoformat(),
        )
        stale = conversation_state.CachedProcurementPlan(
            objective="CHEAPEST",
            items=[],
            result={"success": True},
            saved_at=(datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),
        )

        self.assertTrue(conversation_state.is_plan_valid(fresh))
        self.assertFalse(conversation_state.is_plan_valid(stale))
        self.assertTrue(conversation_state.is_plan_valid({"replenishmentQuantityNeeded": 4}))
        self.assertFalse(conversation_state.is_plan_valid({"success": False}))


if __name__ == "__main__":
    unittest.main()
