"""Plan karşılaştırma sayıları kodda hesaplanır; model aritmetik yapmaz."""
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

import app  # noqa: E402
from prompt import get_reasoning_prompt  # noqa: E402
from test_support import procurement_plan  # noqa: E402


class PlanMetricsTest(unittest.TestCase):
    def test_metrics_extracted_from_plan_result(self):
        metrics = app.plan_metrics(procurement_plan(1000.0, [3, 1], [4.2, 4.9]))

        self.assertEqual(metrics["toplam_maliyet"], 1000.0)
        self.assertEqual(metrics["en_gec_teslimat_gunu"], 3)
        self.assertEqual(metrics["en_erken_teslimat_gunu"], 1)
        self.assertEqual(metrics["en_dusuk_satici_puani"], 4.2)
        self.assertEqual(metrics["en_yuksek_satici_puani"], 4.9)

    def test_cached_plan_shape_is_supported(self):
        cached = {"objective": "FASTEST", "result": procurement_plan(500.0, [1], [4.8])}
        metrics = app.plan_metrics(cached)

        self.assertEqual(metrics["hedef"], "FASTEST")
        self.assertEqual(metrics["toplam_maliyet"], 500.0)

    def test_non_plan_values_are_ignored(self):
        self.assertIsNone(app.plan_metrics({"success": True, "products": [{"id": 1}]}))
        self.assertIsNone(app.plan_metrics("metin"))


class ComparisonTest(unittest.TestCase):
    """Gerçek bir koşuda model 676.700 ile 707.660 arasındaki farkı yanlış hesaplamıştı."""

    def setUp(self):
        self.cleaned = app.clean_tool_results_for_reasoning({
            "step_2": procurement_plan(676700.0, [3, 4, 2, 1, 2], [4.6, 4.2, 4.5, 4.8, 4.4]),
            "step_3": procurement_plan(707660.0, [1, 1, 1, 1, 1], [4.9, 4.8, 4.9, 4.8, 4.9]),
        })
        self.comparison = self.cleaned["hesaplanan_karsilastirma"]

    def test_cost_difference_is_exact(self):
        fark = self.comparison["fark"]
        self.assertEqual(fark["maliyet_farki_TL"], 30960.0)
        self.assertEqual(fark["maliyet_farki_yuzde"], 4.6)
        self.assertEqual(fark["daha_ucuz_olan"], "step_2")
        self.assertEqual(fark["daha_pahali_olan"], "step_3")

    def test_delivery_difference_is_exact(self):
        fark = self.comparison["fark"]
        self.assertEqual(fark["daha_hizli_olan"], "step_3")
        self.assertEqual(fark["teslimat_farki_gun"], 3)

    def test_reasoning_prompt_carries_the_numbers(self):
        prompt = get_reasoning_prompt("En ucuz ve en hızlı planı karşılaştır.", self.cleaned)

        self.assertIn("hesaplanan_karsilastirma", prompt)
        self.assertIn("30960", prompt)
        self.assertIn("ARİTMETİK YAPMA", prompt)

    def test_safe_formatter_uses_host_computed_comparison(self):
        answer = app.format_plan_comparison_fallback(self.cleaned)

        self.assertIn("En ucuz plan", answer)
        self.assertIn("676.700,00 TL", answer)
        self.assertIn("30.960,00 TL daha ucuz", answer)
        self.assertIn("En hızlı plan", answer)
        self.assertIn("3 gün daha hızlı", answer)


class ComparisonEdgeCaseTest(unittest.TestCase):
    def test_single_plan_has_metrics_but_no_difference(self):
        cleaned = app.clean_tool_results_for_reasoning({"step_1": procurement_plan(1000.0, [2], [4.0])})
        comparison = cleaned["hesaplanan_karsilastirma"]

        self.assertIn("step_1", comparison["planlar"])
        self.assertNotIn("fark", comparison)

    def test_results_without_plans_are_untouched(self):
        cleaned = app.clean_tool_results_for_reasoning(
            {"step_1": {"success": True, "products": [{"id": 1}]}})

        self.assertNotIn("hesaplanan_karsilastirma", cleaned)


if __name__ == "__main__":
    unittest.main()
