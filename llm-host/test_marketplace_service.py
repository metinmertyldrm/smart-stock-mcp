"""Marketplace draft writes verify offer/product relationships first."""
import asyncio
import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKETPLACE = os.path.join(BASE_DIR, "marketplace-mcp")


def load_marketplace_services():
    previous_models = sys.modules.get("models")
    sys.path.insert(0, MARKETPLACE)
    try:
        models_spec = importlib.util.spec_from_file_location(
            "models",
            os.path.join(MARKETPLACE, "models.py"),
        )
        models_module = importlib.util.module_from_spec(models_spec)
        sys.modules["models"] = models_module
        models_spec.loader.exec_module(models_module)
        spec = importlib.util.spec_from_file_location(
            "marketplace_services_under_test",
            os.path.join(MARKETPLACE, "services.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(MARKETPLACE)
        if previous_models is None:
            sys.modules.pop("models", None)
        else:
            sys.modules["models"] = previous_models


class MarketplaceDraftAssociationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.module = load_marketplace_services()
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"Optional marketplace dependency missing: {exc}")

    def test_wrong_product_offer_pair_is_rejected_before_database_write(self):
        service = self.module.MarketplaceService("http://stock-service:8081")
        service.get_offers_by_product_id = AsyncMock(
            return_value=[SimpleNamespace(id=9001)],
        )

        with patch.object(self.module.httpx, "AsyncClient") as http_client:
            with self.assertRaisesRegex(
                ValueError,
                "Offer ID 9002 does not belong to product ID 742",
            ):
                asyncio.run(service.create_purchase_draft([{
                    "product_id": 742,
                    "offer_id": 9002,
                    "quantity": 1,
                }]))

        http_client.assert_not_called()

    def test_reject_and_delete_use_draft_lifecycle_endpoints(self):
        service = self.module.MarketplaceService("http://stock-service:8081")
        client = SimpleNamespace(
            post=AsyncMock(return_value=SimpleNamespace(
                raise_for_status=Mock(),
                json=lambda: {
                    "id": 12,
                    "totalCost": 100.0,
                    "status": "REJECTED",
                    "items": [],
                },
            )),
            delete=AsyncMock(return_value=SimpleNamespace(raise_for_status=Mock())),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch.object(self.module.httpx, "AsyncClient", return_value=context):
            rejected = asyncio.run(service.reject_purchase_draft(12))
            asyncio.run(service.delete_purchase_draft(12))

        self.assertEqual(rejected.status, "REJECTED")
        client.post.assert_awaited_once_with(
            "http://stock-service:8081/api/marketplace/drafts/12/reject"
        )
        client.delete.assert_awaited_once_with(
            "http://stock-service:8081/api/marketplace/drafts/12"
        )


if __name__ == "__main__":
    unittest.main()
