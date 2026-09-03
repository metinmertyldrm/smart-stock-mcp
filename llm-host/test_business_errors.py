"""Aracin yazdigi aciklama kullaniciya ulasmali.

Regresyon: 03.09.2026'da "durumu kritik olanlari listele" komutu modelin
category="kritik" yazmasina yol acti. Arac dogru davrandi ve "'kritik' adinda
bir kategori bulunamadi. Mevcut kategoriler: Elektronik." dedi; ama sohbet
katmani bunu genel "Islem tamamlanamadi. Lutfen isteginizi kontrol edip yeniden
deneyin." metniyle degistirdi. Kullanici sorunu kendi komutunda degil sistemde
aradi. Aciklama karar gunlugunde duruyordu, sohbette yoktu.
"""
import ast
import io
import json
import unittest

from test_support import install_optional_stubs

install_optional_stubs()

from plan_execution import normalize_tool_result  # noqa: E402


class BusinessFlagTest(unittest.TestCase):
    def test_business_flag_survives_normalisation(self):
        """Arac isareti ana uygulamaya bozulmadan ulasmali."""
        payload = json.dumps(
            {"success": False, "business": True, "error": "'kritik' adında bir kategori bulunamadı."},
            ensure_ascii=False,
        )
        normalized = normalize_tool_result({"success": False, "business": True, "error": "x"})
        self.assertFalse(normalized.get("success"))
        self.assertTrue(normalized.get("business"))
        self.assertTrue(json.loads(payload)["business"])

    def test_technical_failure_has_no_business_flag(self):
        """Ham ariza is durumu gibi gosterilmemeli."""
        normalized = normalize_tool_result({"success": False, "error": "connection refused"})
        self.assertFalse(normalized.get("business", False))


class FailureBranchTest(unittest.TestCase):
    """Iki yurutme yolu da ayni davranmali; biri guncellenip digeri unutulmamali."""

    FILES = ("plan_execution.py", "agent_runtime.py")

    def test_both_paths_promote_business_errors(self):
        for name in self.FILES:
            with io.open(name, encoding="utf-8") as handle:
                source = handle.read()
            self.assertIn('if normalized.get("business"):', source, name)
            self.assertIn('failure["business_reason"] = tool_error', source, name)
            self.assertIn('failure["retryable"] = False', source, name)

    def test_category_error_marks_itself_as_business(self):
        with io.open("../stock-mcp/tools.py", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        target = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "category_error_response"
        )
        keys = [
            key.value
            for node in ast.walk(target)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant)
        ]
        self.assertIn("business", keys)


if __name__ == "__main__":
    unittest.main()
