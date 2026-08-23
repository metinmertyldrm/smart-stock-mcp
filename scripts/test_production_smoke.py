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
            "llm-host": {
                **base_hardening,
                "ports": [],
                "environment": {
                    "APP_ENV": "production",
                    "APP_VERSION": "local-production",
                    "OLLAMA_MODEL": "qwen3:8b",
                    "LLM_MODEL": "qwen3:8b",
                    "LLM_AUTH_MODE": "local",
                    "LLM_IDENTITY_DB": "/data/identity.db",
                    "LLM_BOOTSTRAP_ADMIN_USERNAME": "smartstock-admin",
                    "LLM_BOOTSTRAP_ADMIN_PASSWORD": "a-long-private-admin-password",
                },
            },
            "web-ui": {
                **base_hardening,
                "ports": [{"host_ip": "127.0.0.1", "published": "8080", "target": 8080}],
                "healthcheck": {
                    "test": ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1"]
                },
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

    def test_gateway_healthcheck_must_use_ipv4_loopback(self):
        config = hardened_config()
        config["services"]["web-ui"]["healthcheck"]["test"] = [
            "CMD-SHELL",
            "wget -qO- http://localhost:8080/healthz >/dev/null || exit 1",
        ]
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_llm_host_must_report_production_environment(self):
        config = hardened_config()
        config["services"]["llm-host"]["environment"]["APP_ENV"] = "development"
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_llm_model_telemetry_must_match_runtime_model(self):
        config = hardened_config()
        config["services"]["llm-host"]["environment"]["LLM_MODEL"] = "other-model"
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_llm_host_must_use_local_identity_mode(self):
        config = hardened_config()
        config["services"]["llm-host"]["environment"]["LLM_AUTH_MODE"] = "anonymous"
        with self.assertRaises(ProductionSmokeFailure):
            audit_compose_config(config)

    def test_llm_host_requires_identity_database_and_bootstrap_credentials(self):
        for name in ("LLM_IDENTITY_DB", "LLM_BOOTSTRAP_ADMIN_USERNAME", "LLM_BOOTSTRAP_ADMIN_PASSWORD"):
            config = hardened_config()
            config["services"]["llm-host"]["environment"][name] = ""
            with self.subTest(name=name), self.assertRaises(ProductionSmokeFailure):
                audit_compose_config(config)


if __name__ == "__main__":
    unittest.main()
