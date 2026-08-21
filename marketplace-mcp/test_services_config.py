import os
import unittest
from unittest.mock import patch

from services import MarketplaceService


class MarketplaceServiceConfigurationTest(unittest.TestCase):
    def test_local_default_is_preserved(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(MarketplaceService().base_url, "http://localhost:8081")

    def test_stock_service_url_environment_override(self):
        with patch.dict(os.environ, {"STOCK_SERVICE_URL": "http://stock-service:8081/"}, clear=True):
            self.assertEqual(MarketplaceService().base_url, "http://stock-service:8081")

    def test_explicit_url_has_priority_over_environment(self):
        with patch.dict(os.environ, {"STOCK_SERVICE_URL": "http://stock-service:8081"}, clear=True):
            self.assertEqual(MarketplaceService("http://example:9999/").base_url, "http://example:9999")


if __name__ == "__main__":
    unittest.main()
