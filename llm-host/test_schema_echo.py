"""Model semanin kendisini deger sanip kopyalarsa argumani at.

Regresyon: 03.09.2026'da "stok durumu kritik olan urunleri listele" komutunda
model list_low_stock'u category={"type": "string"} ile cagirdi. Sema
"Input validation error: {'type': 'string'} is not of type 'string'" diyerek
reddetti ve plan ilk adimda durdu. Kullanici hicbir kategori soylememisti;
deger kullanicidan gelen bir kisit degil, semanin kopyasiydi.
"""
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

from plan_execution import drop_schema_echo_arguments, is_schema_stub  # noqa: E402


class SchemaStubTest(unittest.TestCase):
    def test_recognises_schema_fragments(self):
        self.assertTrue(is_schema_stub({"type": "string"}))
        self.assertTrue(is_schema_stub({"type": "integer", "description": "adet"}))

    def test_leaves_real_values_alone(self):
        """Gercek degerler sema parcasi sayilmamali."""
        self.assertFalse(is_schema_stub("Elektronik"))
        self.assertFalse(is_schema_stub(5))
        self.assertFalse(is_schema_stub({}))
        self.assertFalse(is_schema_stub({"max_total_budget": 50000}))
        # "type" iceren ama sema olmayan gercek bir nesne
        self.assertFalse(is_schema_stub({"type": "URGENT", "product_id": 3}))


class DropSchemaEchoTest(unittest.TestCase):
    def test_drops_the_measured_failure(self):
        cleaned = drop_schema_echo_arguments({"category": {"type": "string"}})
        self.assertEqual(cleaned, {})

    def test_keeps_a_real_category(self):
        """Kullanicinin verdigi kisit atilmamali; bu bir niyet duzeltmesi olurdu."""
        cleaned = drop_schema_echo_arguments({"category": "Elektronik"})
        self.assertEqual(cleaned, {"category": "Elektronik"})

    def test_cleans_inside_filter_objects(self):
        cleaned = drop_schema_echo_arguments(
            {"filters": {"min_rating": {"type": "number"}, "max_delivery_days": 3}}
        )
        self.assertEqual(cleaned, {"filters": {"max_delivery_days": 3}})

    def test_leaves_lists_and_scalars_untouched(self):
        payload = {"order_ids": [1, 2], "quantity": 5, "objective": "CHEAPEST"}
        self.assertEqual(drop_schema_echo_arguments(payload), payload)

    def test_runs_before_the_tool_call(self):
        """Temizleyici argüman hattinda cagrilmali, yoksa hicbir ise yaramaz."""
        with open("plan_execution.py", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("arguments = drop_schema_echo_arguments(arguments)", source)
        self.assertLess(
            source.index("arguments = drop_schema_echo_arguments(arguments)"),
            source.index("raw_result = await client.call_tool(tool_name, arguments)"),
        )


if __name__ == "__main__":
    unittest.main()
