import os
import httpx
from typing import List, Optional
from models import (
    MarketplaceSeller,
    MarketplaceOffer,
    MarketplaceComparisonResponse,
    MarketplacePurchaseDraftResponse,
    MarketplaceOrderResponse
)

class MarketplaceService:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("STOCK_SERVICE_URL", "http://localhost:8081")

    async def list_sellers(self) -> List[MarketplaceSeller]:
        """Fetch all sellers from the marketplace API."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/marketplace/sellers")
            response.raise_for_status()
            return [MarketplaceSeller(**item) for item in response.json()]

    async def search_offers(self, query: Optional[str] = None) -> List[MarketplaceOffer]:
        """Search product offers based on query."""
        async with httpx.AsyncClient() as client:
            params = {}
            if query:
                params["query"] = query
            response = await client.get(f"{self.base_url}/api/marketplace/offers", params=params)
            response.raise_for_status()
            return [MarketplaceOffer(**item) for item in response.json()]

    async def get_offers_by_product_id(self, product_id: int) -> List[MarketplaceOffer]:
        """Fetch marketplace offers for a specific product ID."""
        async with httpx.AsyncClient() as client:
            params = {"productId": product_id}
            response = await client.get(f"{self.base_url}/api/marketplace/offers", params=params)
            response.raise_for_status()
            return [MarketplaceOffer(**item) for item in response.json()]

    async def compare_offers(self, product_id: int) -> MarketplaceComparisonResponse:
        """Compare offers for a specific product ID using TOPSIS scoring."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/marketplace/offers/compare", params={"productId": product_id})
            response.raise_for_status()
            return MarketplaceComparisonResponse(**response.json())

    async def check_availability(self, offer_id: int, quantity: int) -> bool:
        """Check if seller has enough stock for a given offer ID and quantity."""
        async with httpx.AsyncClient() as client:
            params = {"offerId": offer_id, "quantity": quantity}
            response = await client.get(f"{self.base_url}/api/marketplace/check-availability", params=params)
            response.raise_for_status()
            return response.json()

    async def create_purchase_draft(self, items: List[dict]) -> MarketplacePurchaseDraftResponse:
        """Create a draft after verifying every offer/product association.

        `offer_id` values are database-local identifiers. Never trust a model
        or caller to pair one with the intended product: resolve current offers
        for the expected product and reject mismatches before the POST that
        creates persistent data.
        """
        payload_items = []
        offers_by_product = {}
        for item in items:
            product_id = item.get("product_id") or item.get("productId")
            offer_id = item.get("offer_id") or item.get("offerId")
            quantity = item.get("quantity")
            if product_id is None or offer_id is None or not quantity or int(quantity) <= 0:
                raise ValueError(
                    "Each draft item requires a positive product_id, offer_id and quantity."
                )

            product_id = int(product_id)
            offer_id = int(offer_id)
            if product_id not in offers_by_product:
                offers_by_product[product_id] = await self.get_offers_by_product_id(product_id)
            valid_offer_ids = {int(offer.id) for offer in offers_by_product[product_id]}
            if offer_id not in valid_offer_ids:
                raise ValueError(
                    f"Offer ID {offer_id} does not belong to product ID {product_id}."
                )

            payload_items.append({
                "offerId": offer_id,
                "quantity": int(quantity),
            })

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/marketplace/drafts",
                json={"items": payload_items}
            )
            response.raise_for_status()
            return MarketplacePurchaseDraftResponse(**response.json())

    async def place_order(self, draft_id: int) -> MarketplaceOrderResponse:
        """Place a purchase order using draft ID."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/marketplace/orders",
                json={"draftId": draft_id}
            )
            response.raise_for_status()
            return MarketplaceOrderResponse(**response.json())

    async def list_orders(self) -> List[MarketplaceOrderResponse]:
        """Pazaryerinde verilmis tum siparisleri listeler."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/marketplace/orders")
            response.raise_for_status()
            return [MarketplaceOrderResponse(**item) for item in response.json()]

    async def get_order_details(self, order_id: int) -> MarketplaceOrderResponse:
        """Fetch details and status of an order."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/marketplace/orders/{order_id}")
            response.raise_for_status()
            return MarketplaceOrderResponse(**response.json())
