import os
from typing import List, Optional, Union

import httpx

from models import Product, Replenishment, IncomingOrder


class ProductService:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or os.getenv("STOCK_SERVICE_URL", "http://localhost:8081")).rstrip("/")

    async def get_all_products(self) -> List[Product]:
        """Fetch all products from the Spring Boot API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/products")
            response.raise_for_status()
            return [Product(**item) for item in response.json()]

    async def search_products(self, query: str) -> List[Product]:
        """Search products by name, SKU, or description."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/products/search", params={"query": query})
            response.raise_for_status()
            return [Product(**item) for item in response.json()]

    async def get_out_of_stock_products(self) -> List[Product]:
        """Fetch out-of-stock products (quantity = 0)."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/products/out-of-stock")
            response.raise_for_status()
            return [Product(**item) for item in response.json()]

    async def get_low_stock_products(self) -> List[Product]:
        """Fetch low-stock products (quantity <= min_stock)."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/products/low-stock")
            response.raise_for_status()
            return [Product(**item) for item in response.json()]

    async def calculate_replenishment(self, product_id: Optional[int] = None, product_ids: Optional[Union[int, List[int]]] = None) -> List[Replenishment]:
        """Calculate quantity to order for products below minimum stock."""
        params = {}
        if product_ids is not None:
            if isinstance(product_ids, int):
                params["productIds"] = [product_ids]
            elif isinstance(product_ids, list):
                params["productIds"] = product_ids
        elif product_id is not None:
            params["productIds"] = [product_id]

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/products/replenishment", params=params)
            response.raise_for_status()
            replenishments = [Replenishment(**item) for item in response.json()]
            return replenishments

    async def create_incoming_order(self, product_id: int, quantity: int, expected_delivery_date: Optional[str] = None) -> IncomingOrder:
        """Create a pending incoming/replenishment order."""
        async with httpx.AsyncClient() as client:
            payload = {"productId": product_id, "quantity": quantity}
            normalized = self._normalize_expected(expected_delivery_date)
            if normalized:
                payload["expectedDeliveryDate"] = normalized

            response = await client.post(f"{self.base_url}/api/orders", json=payload)
            response.raise_for_status()
            return IncomingOrder(**response.json())

    async def list_incoming_orders(self, pending_only: bool = True, ready_only: bool = False) -> List[IncomingOrder]:
        """Depoya beklenen siparisleri listeler (varsayilan: yalnizca PENDING)."""
        path = "/api/orders/pending" if pending_only else "/api/orders"
        params = {"readyOnly": "true"} if pending_only and ready_only else None
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return [IncomingOrder(**item) for item in response.json()]

    @staticmethod
    def _normalize_expected(value: Optional[str]) -> Optional[str]:
        """Backend LocalDateTime bekler; "2026-08-23" gibi gun-only deger 400 uretir."""
        if not value:
            return None
        return value if "T" in value else f"{value}T00:00:00"

    async def create_incoming_orders(self, items: List[dict]) -> List[IncomingOrder]:
        """Create one pending incoming order per item (batch helper)."""
        created: List[IncomingOrder] = []
        async with httpx.AsyncClient() as client:
            for item in items:
                payload = {
                    "productId": item.get("product_id") or item.get("productId"),
                    "quantity": item.get("quantity"),
                }
                expected = self._normalize_expected(
                    item.get("expected_delivery_date") or item.get("expectedDeliveryDate")
                )
                if expected:
                    payload["expectedDeliveryDate"] = expected

                response = await client.post(f"{self.base_url}/api/orders", json=payload)
                response.raise_for_status()
                created.append(IncomingOrder(**response.json()))
        return created

    async def receive_order(self, order_id: int) -> IncomingOrder:
        """Mark an incoming order as received, increasing product stock."""
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/api/orders/{order_id}/receive")
            response.raise_for_status()
            return IncomingOrder(**response.json())

    async def receive_orders(self, order_ids: List[int]) -> tuple[List[IncomingOrder], List[dict]]:
        """Receive independently so one unavailable delivery does not hide the others."""
        received: List[IncomingOrder] = []
        failed: List[dict] = []
        for order_id in order_ids:
            try:
                received.append(await self.receive_order(order_id))
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                failed.append({
                    "order_id": order_id,
                    "status": exc.response.status_code,
                    "error": detail or "Teslimat stoğa alınamadı.",
                })
            except Exception as exc:
                failed.append({"order_id": order_id, "error": str(exc)})
        return received, failed
