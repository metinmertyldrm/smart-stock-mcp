"""Static safety contracts shared by Spring and the acceptance reset wrapper."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APPLICATION = ROOT / "stock-service/src/main/resources/application.yml"
ACCEPTANCE = ROOT / "stock-service/src/main/resources/application-acceptance.yml"
RESET = ROOT / "stock-service/scripts/reset-acceptance.ps1"


class SpringTopologyTest(unittest.TestCase):
    def test_default_port_and_override_contract(self):
        self.assertIn("port: ${SERVER_PORT:8081}", APPLICATION.read_text(encoding="utf-8"))

    def test_normal_database_override_preserves_safe_default(self):
        self.assertIn(
            "${DB_URL:jdbc:postgresql://localhost:5432/smart_stock}",
            APPLICATION.read_text(encoding="utf-8"))
        self.assertIn("ddl-auto: ${DB_DDL_AUTO:update}", APPLICATION.read_text(encoding="utf-8"))

    def test_acceptance_database_has_an_isolated_default(self):
        self.assertIn(
            "${DB_URL:jdbc:postgresql://localhost:5432/smart_stock_acceptance}",
            ACCEPTANCE.read_text(encoding="utf-8"))


class ResetSafetyContractTest(unittest.TestCase):
    def test_reset_uses_db_url_and_rejects_non_acceptance_names(self):
        script = RESET.read_text(encoding="utf-8")
        self.assertIn("$env:DB_URL", script)
        self.assertNotIn("ACCEPTANCE_DB_NAME", script)
        self.assertIn('EndsWith("_acceptance"', script)
        self.assertIn("Refusing destructive reset", script)

    def test_reset_and_seed_are_one_error_stopping_transaction(self):
        script = RESET.read_text(encoding="utf-8")
        self.assertIn("ON_ERROR_STOP=1", script)
        self.assertIn("--single-transaction", script)
        self.assertEqual(script.count("& psql"), 1)


if __name__ == "__main__":
    unittest.main()
