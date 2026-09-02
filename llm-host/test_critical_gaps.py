"""27.08 kabul ölçümünde kalan kritik açıkların regresyon testleri.

Ölçümde düşen beş senaryonun tamamı aynı aileden geliyordu: model argümanı
yanlış yere ya da yanlış biçimde yazıyor, MCP şeması çağrıyı reddediyor.
Bu dosya o ailenin üç kolunu kapatan düzeltmeleri sabitler.
"""
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

from plan_execution import (  # noqa: E402
    COLLECTION_ARGUMENTS, drop_empty_optional_arguments)
from plan_validation import (  # noqa: E402
    parse_execution_plan, validate_incoming_orders_source)
import json  # noqa: E402


class EmptyOptionalArgumentTest(unittest.TestCase):
    """Regresyon: `compare_offers(quantity=[], min_rating=[])` şemaya takılıyordu.

    Boş değer hiçbir kısıt ifade etmez; atmak bilgi kaybettirmez. Bu bir ŞEKİL
    düzeltmesidir, kullanıcının niyetine dokunmaz.
    """

    def test_empty_filters_are_dropped(self):
        result = drop_empty_optional_arguments("compare_offers", {
            "product_id": 2, "quantity": [], "min_rating": [],
            "max_delivery_days": [], "objective": "CHEAPEST"})

        self.assertEqual(result, {"product_id": 2, "objective": "CHEAPEST"})

    def test_empty_object_is_dropped(self):
        result = drop_empty_optional_arguments(
            "create_procurement_plan", {"items": [{"product_id": 1}], "filters": {}})

        self.assertEqual(result, {"items": [{"product_id": 1}]})

    def test_meaningful_values_survive(self):
        arguments = {"product_id": 2, "quantity": 5, "min_rating": 4.5}

        self.assertEqual(
            drop_empty_optional_arguments("compare_offers", arguments), arguments)

    def test_zero_and_false_are_not_dropped(self):
        """0 ve False bos degil; atilirsa kullanicinin kisiti kaybolur."""
        arguments = {"product_id": 2, "max_shipping_cost": 0, "include_partial": False}

        self.assertEqual(
            drop_empty_optional_arguments("compare_offers", arguments), arguments)

    def test_collection_argument_keeps_its_empty_list(self):
        """Bos koleksiyon bir is durumudur; detect_empty_input onu acikliyor."""
        for tool, key in COLLECTION_ARGUMENTS.items():
            with self.subTest(tool=tool):
                result = drop_empty_optional_arguments(tool, {key: []})
                self.assertEqual(result, {key: []})


class IncomingOrdersSourceTest(unittest.TestCase):
    """Regresyon: model `expected_delivery_date='2023-10-15'` uyduruyordu.

    Doğru tarih zaten place_order sonucunda var; adım oradan türetilmeli.
    """

    ORDER_STEP = {"id": "step_1", "tool": "place_order",
                  "arguments": {"draft_id": {"$from_context": "pending_draft_id"}}}

    def derived_step(self):
        return {"id": "step_2", "tool": "create_incoming_orders", "arguments": {
            "items": {"$from": "step_1", "$transform": "order_to_incoming_items"}}}

    def literal_step(self):
        return {"id": "step_2", "tool": "create_incoming_orders", "arguments": {
            "items": [{"product_id": 1, "quantity": 8,
                       "expected_delivery_date": "2023-10-15"}]}}

    def test_derived_chain_is_accepted(self):
        validate_incoming_orders_source([self.ORDER_STEP, self.derived_step()])

    def test_hand_written_items_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_incoming_orders_source([self.ORDER_STEP, self.literal_step()])

        message = str(ctx.exception)
        self.assertIn("order_to_incoming_items", message)
        self.assertIn("place_order", message)

    def test_reference_to_the_wrong_step_is_rejected(self):
        wrong = {"id": "step_2", "tool": "create_incoming_orders", "arguments": {
            "items": {"$from": "step_9", "$transform": "order_to_incoming_items"}}}

        with self.assertRaises(ValueError):
            validate_incoming_orders_source([self.ORDER_STEP, wrong])

    def test_step_without_place_order_is_not_constrained(self):
        """Siparis adimi olmayan bir zincirde bu kural uygulanmaz."""
        validate_incoming_orders_source([self.literal_step()])

    def test_rule_runs_inside_plan_parsing(self):
        """Kural plan ayristirmasinda da devrede olmali, yalnizca dogrudan cagrida degil."""
        plan = json.dumps({"type": "execution_plan", "goal": "ORDER",
                           "steps": [self.ORDER_STEP, self.literal_step()]})

        with self.assertRaises(ValueError):
            parse_execution_plan(plan)

    def test_valid_order_plan_still_parses(self):
        plan = json.dumps({"type": "execution_plan", "goal": "ORDER",
                           "steps": [self.ORDER_STEP, self.derived_step()]})

        parsed = parse_execution_plan(plan)

        self.assertEqual(parsed["goal"], "ORDER")
        self.assertEqual(len(parsed["steps"]), 2)


if __name__ == "__main__":
    unittest.main()
