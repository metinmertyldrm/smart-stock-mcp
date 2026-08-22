import unittest

from production_smoke import ProductionSmokeFailure, audit_compose_config


def hardened_config():
    base_hardening = {
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    return {
        "services": {
            "postgres": {"ports": []},
            "stock-service": {
                **base_hardening,
                "ports": [],
                "environment": {"SPRING_PROFILES_ACTIVE": "production"},
            },
            "ollama": {"ports": []},
            "ollama-init": {"ports": []},
            "llm-host": {**base_hardening, "ports": []},
            "web-ui": {
                **base_hardening,
                "ports": [{"host_ip": "127.0.0.1", "published": "8080", "target": 8080}],
            },
        }
    }


class ProductionComposeAuditTest(unittest.TestCase):
    def test_hardened_contract_passes(self):
        audit_compose_config(hardened_config())

    def test_backend_port_publish_is_rejected(self):
        config = hardened_config()
        config["services"]["llm-host"]["ports"] = [{"published": "8000", "target": 8000}]
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_public_gateway_bind_requires_explicit_allowance(self):
        config = hardened_config()
        config["services"]["web-ui"]["ports"][0]["host_ip"] = "0.0.0.0"
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)
        audit_compose_config(config, allow_public_bind=True)

    def test_missing_read_only_is_rejected(self):
        config = hardened_config()
        config["services"]["stock-service"]["read_only"] = False
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_missing_cap_drop_is_rejected(self):
        config = hardened_config()
        config["services"]["web-ui"]["cap_drop"] = []
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_wrong_spring_profile_is_rejected(self):
        config = hardened_config()
        config["services"]["stock-service"]["environment"]["SPRING_PROFILES_ACTIVE"] = "default"
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)


if __name__ == "__main__":
    unittest.main()
