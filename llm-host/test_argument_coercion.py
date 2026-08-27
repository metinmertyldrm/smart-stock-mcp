"""Koleksiyon argümanına tekil değer geldiğinde sarmalama.

Regresyon (27.08 kabul koşumu): model üç koşumda da
`receive_orders(order_ids=1)` üretti, MCP şeması dizi beklediği için
"1 is not of type 'array'" dedi ve senaryo 0/3 düştü.

Sınır: yalnızca ŞEKİL düzeltilir. Yanlış araca giden argüman (ör. marketplace
filtresinin stok aracına yazılması) burada düzeltilmez — sessizce düzeltmek
kullanıcının kısıtını kaybettirir, onarım döngüsüne bırakılır.
"""
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

from plan_execution import (  # noqa: E402
    COLLECTION_ARGUMENTS, coerce_collection_arguments)


class CoerceCollectionArgumentsTest(unittest.TestCase):
    def test_scalar_order_id_becomes_a_list(self):
        result = coerce_collection_arguments("receive_orders", {"order_ids": 1})

        self.assertEqual(result, {"order_ids": [1]})

    def test_existing_list_is_left_alone(self):
        arguments = {"order_ids": [1, 2]}

        self.assertEqual(
            coerce_collection_arguments("receive_orders", arguments), arguments)

    def test_single_item_object_becomes_a_list(self):
        result = coerce_collection_arguments(
            "create_procurement_plan", {"items": {"product_id": 1, "quantity": 5}})

        self.assertEqual(result["items"], [{"product_id": 1, "quantity": 5}])

    def test_other_arguments_are_preserved(self):
        result = coerce_collection_arguments(
            "create_procurement_plan",
            {"items": {"product_id": 1}, "objective": "CHEAPEST"})

        self.assertEqual(result["objective"], "CHEAPEST")

    def test_none_is_not_wrapped(self):
        """None'i [None] yapmak bos girdi korumasini atlatirdi."""
        arguments = {"order_ids": None}

        self.assertEqual(
            coerce_collection_arguments("receive_orders", arguments), arguments)

    def test_empty_list_stays_empty(self):
        """Bos liste is durumudur; detect_empty_input onu acikliyor."""
        arguments = {"items": []}

        self.assertEqual(
            coerce_collection_arguments("create_procurement_plan", arguments), arguments)

    def test_tool_without_a_collection_argument_is_untouched(self):
        arguments = {"query": "iPhone"}

        self.assertEqual(
            coerce_collection_arguments("search_products", arguments), arguments)

    def test_missing_collection_argument_is_untouched(self):
        arguments = {"objective": "CHEAPEST"}

        self.assertEqual(
            coerce_collection_arguments("create_procurement_plan", arguments), arguments)

    def test_input_is_not_mutated(self):
        """Cagiran taraf orijinal argumanlari izde gosteriyor; bozmamaliyiz."""
        arguments = {"order_ids": 1}

        coerce_collection_arguments("receive_orders", arguments)

        self.assertEqual(arguments, {"order_ids": 1})

    def test_every_collection_tool_is_covered(self):
        for tool, key in COLLECTION_ARGUMENTS.items():
            with self.subTest(tool=tool):
                result = coerce_collection_arguments(tool, {key: 7})
                self.assertEqual(result[key], [7])


class WrongToolArgumentsAreNotSilentlyFixedTest(unittest.TestCase):
    """Kullanıcının kısıtı sessizce kaybolmamalı."""

    def test_marketplace_filter_on_a_stock_tool_is_left_for_repair(self):
        arguments = {"filters": {"max_delivery_days": 3}}

        self.assertEqual(
            coerce_collection_arguments("calculate_replenishment", arguments), arguments)

    def test_category_on_a_tool_without_it_is_left_for_repair(self):
        arguments = {"category": "elektronik"}

        self.assertEqual(
            coerce_collection_arguments("list_low_stock", arguments), arguments)


if __name__ == "__main__":
    unittest.main()
