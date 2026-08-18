from mcp.server import Server
from mcp.types import Tool, TextContent
import asyncio
import logging
import os
import sys
from services import MarketplaceService
import json

# MCP stdio protokolunde sunucunun STDOUT'u JSON-RPC kanalidir.
# Oraya yazilan her satir protokolu bozar ve gercek cevaplarla birlesip
# mesaj kaybina yol acar. Teshis ciktilari bu yuzden stderr'e gider.
logging.basicConfig(
    stream=sys.stderr,
    level=os.getenv("MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("marketplace-server")

# Initialize Server and Service
server = Server("marketplace-server")
service = MarketplaceService()

def satisfies_rating(rating: float, min_rating: float | None) -> bool:
    """Check if rating is higher than or equal to min_rating."""
    return min_rating is None or rating >= min_rating

def filter_marketplace_offers(
    offers,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    max_delivery_days: int | None = None,
    max_shipping_cost: float | None = None,
    min_available_stock: int | None = None
):
    """Filter marketplace offers based on various criteria."""
    # Clean default/invalid filter values
    is_default_zeros = (
        (min_rating == 0 or min_rating is None) and
        (max_delivery_days == 0 or max_delivery_days is None) and
        (max_shipping_cost == 0 or max_shipping_cost is None)
    )
    if is_default_zeros:
        min_rating = None
        max_delivery_days = None
        max_shipping_cost = None
    else:
        if min_rating is not None and min_rating <= 0:
            min_rating = None
        if max_delivery_days is not None and max_delivery_days <= 0:
            max_delivery_days = None

    filtered = list(offers)
    if min_price is not None:
        filtered = [o for o in filtered if o.price >= min_price]
    if max_price is not None:
        filtered = [o for o in filtered if o.price <= max_price]
    if min_rating is not None:
        filtered = [o for o in filtered if satisfies_rating(o.seller.rating, min_rating)]
    if max_delivery_days is not None:
        filtered = [o for o in filtered if o.deliveryTimeDays <= max_delivery_days]
    if max_shipping_cost is not None:
        filtered = [o for o in filtered if o.shippingFee <= max_shipping_cost]
    if min_available_stock is not None:
        filtered = [o for o in filtered if o.stockQuantity >= min_available_stock]
    return filtered


def allocate_across_offers(
    offers: list[dict],
    requested_quantity: int,
    objective: str,
    filters: dict
) -> dict:
    min_rating = filters.get("min_rating")
    max_delivery_days = filters.get("max_delivery_days")
    max_unit_price = filters.get("max_unit_price")
    max_shipping_cost = filters.get("max_shipping_cost")

    # Clean default/invalid filter values
    is_default_zeros = (
        (min_rating == 0 or min_rating is None) and
        (max_delivery_days == 0 or max_delivery_days is None) and
        (max_unit_price == 0 or max_unit_price is None) and
        (max_shipping_cost == 0 or max_shipping_cost is None)
    )
    if is_default_zeros:
        min_rating = None
        max_delivery_days = None
        max_unit_price = None
        max_shipping_cost = None
    else:
        if min_rating is not None and min_rating <= 0:
            min_rating = None
        if max_delivery_days is not None and max_delivery_days <= 0:
            max_delivery_days = None
        if max_unit_price is not None and max_unit_price <= 0:
            max_unit_price = None

    eligible_offers = []
    for offer in offers:
        if offer["available_stock"] <= 0:
            continue

        if min_rating is not None and not satisfies_rating(offer["rating"], min_rating):
            continue

        if (
            max_delivery_days is not None
            and offer["delivery_days"] > max_delivery_days
        ):
            continue

        if (
            max_unit_price is not None
            and offer["unit_price"] > max_unit_price
        ):
            continue

        if (
            max_shipping_cost is not None
            and offer["shipping_cost"] > max_shipping_cost
        ):
            continue

        eligible_offers.append(offer)

    if objective == "CHEAPEST":
        eligible_offers.sort(
            key=lambda x: (
                x["unit_price"],
                x["shipping_cost"],
                -x["rating"],
                x["delivery_days"]
            )
        )
    elif objective == "FASTEST":
        eligible_offers.sort(
            key=lambda x: (
                x["delivery_days"],
                x["unit_price"],
                -x["rating"]
            )
        )
    elif objective == "HIGHEST_RATED":
        eligible_offers.sort(
            key=lambda x: (
                -x["rating"],
                x["delivery_days"],
                x["unit_price"]
            )
        )
    elif objective == "BALANCED":
        eligible_offers.sort(
            key=lambda x: x["topsis_score"],
            reverse=True
        )

    remaining = requested_quantity
    allocations = []
    total_cost = 0.0

    for offer in eligible_offers:
        if remaining <= 0:
            break

        quantity = min(offer["available_stock"], remaining)
        if quantity == 0:
            continue

        subtotal = (
            quantity * offer["unit_price"]
            + offer["shipping_cost"]
        )

        allocations.append({
            "offer_id": offer["offer_id"],
            "seller_name": offer["seller_name"],
            "quantity": quantity,
            "unit_price": offer["unit_price"],
            "shipping_cost": offer["shipping_cost"],
            "subtotal": subtotal,
            "rating": offer["rating"],
            "delivery_days": offer["delivery_days"]
        })

        total_cost += subtotal
        remaining -= quantity

    return {
        "success": len(allocations) > 0,
        "complete": remaining == 0,
        "requested_quantity": requested_quantity,
        "fulfilled_quantity": requested_quantity - remaining,
        "missing_quantity": remaining,
        "allocations": allocations,
        "overall_total": total_cost,
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="list_sellers",
            description="List and filter marketplace sellers with their rating and base delivery times.",
            inputSchema={
                "type": "object",
                "properties": {
                    "min_rating": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Minimum seller rating, inclusive."
                    },
                    "max_delivery_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum base delivery duration in days."
                    }
                },
                "additionalProperties": False,
            }
        ),
        Tool(
            name="search_offers",
            description=(
                "Search and filter marketplace offers. "
                "Use product_id for a known inventory product or query for free-text search. "
                "Optional filters can restrict price, seller rating, delivery time, "
                "shipping cost and available stock. "
                "If both product_id and query are provided, product_id takes precedence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product name or keyword."
                    },
                    "product_id": {
                        "type": "integer",
                        "description": "Exact inventory product ID."
                    },
                    "min_price": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Minimum unit price, inclusive."
                    },
                    "max_price": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Maximum unit price, inclusive."
                    },
                    "min_rating": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Minimum seller rating, inclusive."
                    },
                    "max_delivery_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum delivery duration in days."
                    },
                    "max_shipping_cost": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Maximum shipping cost."
                    },
                    "min_available_stock": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Minimum available offer stock."
                    }
                },
                "anyOf": [
                    {"required": ["product_id"]},
                    {"required": ["query"]}
                ],
                "additionalProperties": False
            }
        ),
        Tool(
            name="compare_offers",
            description=(
                "Compare marketplace offers for exactly one product. Apply any provided constraints "
                "(such as minimum seller rating, maximum delivery time, shipping cost, or available stock) "
                "to filter eligible offers. Then rank the remaining offers according to the user's objective "
                "(for example, cheapest, fastest, or highest rated) and return the most suitable offer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "Product ID to compare offers for."
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Quantity needed to ensure sufficient stock."
                    },
                    "objective": {
                        "type": "string",
                        "enum": [
                            "CHEAPEST",
                            "FASTEST",
                            "HIGHEST_RATED"
                        ],
                        "default": "CHEAPEST",
                        "description": "Sorting objective for ranking eligible offers."
                    },
                    "min_rating": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Minimum seller rating filter. Seller rating must be strictly greater than this value (exclusive)."
                    },
                    "max_delivery_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum delivery duration filter in days."
                    },
                    "max_shipping_cost": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Maximum shipping cost filter."
                    },
                    "exclude_offer_ids": {
                        "type": "array",
                        "items": {
                            "type": "integer"
                        },
                        "description": "Optional list of offer IDs to exclude from comparison."
                    }
                },
                "required": [
                    "product_id",
                    "quantity"
                ],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="check_availability",
            description="Check whether the requested quantity of a marketplace offer is currently available by its offer ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "offer_id": {
                        "type": "integer",
                        "description": "Offer ID to check availability for."
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Quantity to check availability for."
                    },
                },
                "required": ["offer_id", "quantity"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="create_purchase_draft",
            description=(
                "Use this tool when the user wants to prepare a purchase but not finalize it. "
                "This tool only creates a draft and does not place an order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Marketplace offers to add to the purchase draft.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "offer_id": {
                                    "type": "integer",
                                    "description": "Marketplace offer ID."
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": "Quantity to purchase."
                                }
                            },
                            "required": ["offer_id", "quantity"],
                            "additionalProperties": False,
                        }
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="place_order",
            description="Place a purchase order from an existing purchase draft. Use this tool when the user wants to finalize, confirm, approve, or place a previously created purchase draft.",
            inputSchema={
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "integer",
                        "description": "Purchase draft ID to place the order for."
                    },
                },
                "required": ["draft_id"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="get_order_status",
            description="Use ONLY when the user explicitly asks to check, view, or track an existing order.",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "The ID of the order to get the status of."
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            }
        ),    
        Tool(
            name="create_procurement_plan",
            description=(
                "Create an optimized procurement plan for multiple products. "
                "Calculates the optimal allocations across multiple sellers, "
                "splitting the required quantity if needed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of products and required quantities.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {
                                    "type": "integer",
                                    "description": "Product ID."
                                },
                                "quantity": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Required quantity."
                                }
                            },
                            "required": ["product_id", "quantity"],
                            "additionalProperties": False
                        }
                    },
                    "objective": {
                        "type": "string",
                        "enum": ["CHEAPEST", "FASTEST", "HIGHEST_RATED", "BALANCED"],
                        "default": "CHEAPEST",
                        "description": "Objective used to optimize offer allocations ( CHEAPEST:Minimize total cost, FASTEST:Minimize delivery time, HIGHEST_RATED:prioritizes higher seller ratings, BALANCED:Balance cost and delivery time)"
                    },
                    "filters": {
                        "type": "object",
                        "properties": {
                             "min_rating": {
                                 "type": ["number", "null"],
                                 "minimum": 0,
                                 "maximum": 5,
                                 "description": "Minimum seller rating filter. Seller rating must be strictly greater than this value (exclusive)."
                             },
                            "max_delivery_days": {
                                "type": ["integer", "null"],
                                "minimum": 0,
                                "description": "Maximum delivery duration filter in days."
                            },
                            "max_unit_price": {
                                "type": ["number", "null"],
                                "minimum": 0,
                                "description": "Maximum unit price filter."
                            },
                            "max_shipping_cost": {
                                "type": ["number", "null"],
                                "minimum": 0,
                                "description": "Maximum shipping cost filter."
                            }
                        },
                        "additionalProperties": False,
                        "description": "Optional filters to restrict eligible offers."
                    }
                },
                "required": ["items"],
                "additionalProperties": False
            }
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Execute the tool selected by LLM using MarketplaceService"""
    arguments = arguments or {}
    try: 
        if name == "list_sellers":
            sellers = await service.list_sellers()
            min_rating = arguments.get("min_rating")
            max_delivery_days = arguments.get("max_delivery_days")
            if min_rating is not None:
                sellers = [s for s in sellers if s.rating is not None and s.rating >= min_rating]
            if max_delivery_days is not None:
                sellers = [s for s in sellers if s.baseDeliveryDays is not None and s.baseDeliveryDays <= max_delivery_days]
            result = {
                "success": True,
                "count": len(sellers),
                "sellers": [
                    seller.model_dump()
                    for seller in sellers
                ]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "search_offers":
            query = arguments.get("query")
            product_id = arguments.get("product_id")
            min_price = arguments.get("min_price")
            max_price = arguments.get("max_price")
            min_rating = arguments.get("min_rating")
            max_delivery_days = arguments.get("max_delivery_days")
            max_shipping_cost = arguments.get("max_shipping_cost")
            min_available_stock = arguments.get("min_available_stock")

            if query is None and product_id is None:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "success": False,
                                "error": "Either query or product_id must be provided."
                            },
                            ensure_ascii=False
                        )
                    )
                ]

            if product_id is not None:
                offers = await service.get_offers_by_product_id(product_id)
            else:
                offers = await service.search_offers(query)

            # Apply optional filters using helper function
            offers = filter_marketplace_offers( 
                offers,
                min_price=min_price,
                max_price=max_price,
                min_rating=min_rating,
                max_delivery_days=max_delivery_days,
                max_shipping_cost=max_shipping_cost,
                min_available_stock=min_available_stock
            )

            result = {
                "success": True,
                "query": query,
                "product_id": product_id,
                "count": len(offers),
                "offers": [
                    offer.model_dump()
                    for offer in offers
                ]
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "compare_offers":
            product_id = arguments.get("product_id")
            quantity = arguments.get("quantity")
            objective = arguments.get("objective", "CHEAPEST")
            min_rating = arguments.get("min_rating")
            max_delivery_days = arguments.get("max_delivery_days")
            max_shipping_cost = arguments.get("max_shipping_cost")
            exclude_offer_ids = arguments.get("exclude_offer_ids", [])

            if product_id is None:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No product ID provided."}, ensure_ascii=False)
                    )
                ]
            if quantity is None:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No quantity provided."}, ensure_ascii=False)
                    )
                ]

            if objective not in ["CHEAPEST", "FASTEST", "HIGHEST_RATED"]:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": f"Invalid objective: {objective}"}, ensure_ascii=False)
                    )
                ]

            try:
                # 1. Fetch marketplace offers for the product
                offers = await service.get_offers_by_product_id(product_id)
            except Exception as e:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": f"Error fetching offers: {str(e)}"}, ensure_ascii=False)
                    )
                ]

            # 2. Filtering
            # Pass min_available_stock=quantity to check sufficient stock
            eligible_offers = filter_marketplace_offers(
                offers,
                min_rating=min_rating,
                max_delivery_days=max_delivery_days,
                max_shipping_cost=max_shipping_cost,
                min_available_stock=quantity
            )

            if exclude_offer_ids:
                eligible_offers = [o for o in eligible_offers if o.id not in exclude_offer_ids]

            # Keep track of filter applications for the reason description
            applied_filters = [f"yeterli stok (en az {quantity})"]
            if min_rating is not None:
                applied_filters.append(f"en az {min_rating} satıcı puanı")
            if max_delivery_days is not None:
                applied_filters.append(f"en fazla {max_delivery_days} gün teslimat süresi")
            if max_shipping_cost is not None:
                applied_filters.append(f"en fazla {max_shipping_cost} TL kargo ücreti")
            if exclude_offer_ids:
                applied_filters.append(f"hariç tutulan teklifler ({len(exclude_offer_ids)} adet)")

            if not eligible_offers:
                filters_str = ", ".join(applied_filters)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "success": True,
                            "match_found": False,
                            "selected_offer": None,
                            "total_cost": 0.0,
                            "reason": f"Belirtilen kriterleri ({filters_str}) karşılayan uygun bir teklif bulunamadı.",
                            "alternatives": []
                        }, ensure_ascii=False)
                    )
                ]

            # Helper for calculating total cost
            def total_cost(offer, qty):
                return offer.price * qty + offer.shippingFee

            # 3. Deterministic Ranking
            if objective == "CHEAPEST":
                eligible_offers.sort(
                    key=lambda o: (
                        total_cost(o, quantity),
                        o.deliveryTimeDays,
                        -o.seller.rating,
                        o.id
                    )
                )
            elif objective == "FASTEST":
                eligible_offers.sort(
                    key=lambda o: (
                        o.deliveryTimeDays,
                        total_cost(o, quantity),
                        -o.seller.rating,
                        o.id
                    )
                )
            elif objective == "HIGHEST_RATED":
                eligible_offers.sort(
                    key=lambda o: (
                        -o.seller.rating,
                        total_cost(o, quantity),
                        o.deliveryTimeDays,
                        o.id
                    )
                )

            selected_offer = eligible_offers[0]
            alternatives = eligible_offers[1:]
            selected_total_cost = total_cost(selected_offer, quantity)

            # Construct reasoning string
            filters_clause = " ve ".join(applied_filters)
            
            if objective == "CHEAPEST":
                reason = (
                    f"Seçilen teklif, {filters_clause} kriterlerini karşılayan "
                    f"ve toplam maliyeti en ucuz olan ({selected_offer.price} TL * {quantity} adet + "
                    f"{selected_offer.shippingFee} TL kargo = {selected_total_cost:.2f} TL) tekliftir."
                )
            elif objective == "FASTEST":
                reason = (
                    f"Seçilen teklif, {filters_clause} kriterlerini karşılayan, "
                    f"en hızlı teslimat süresine sahip ({selected_offer.deliveryTimeDays} gün) "
                    f"ve teslimat süresi eşitliği durumunda en ucuz olan tekliftir."
                )
            elif objective == "HIGHEST_RATED":
                reason = (
                    f"Seçilen teklif, {filters_clause} kriterlerini karşılayan, "
                    f"satıcı puanı en yüksek olan ({selected_offer.seller.rating}/5) "
                    f"ve puan eşitliği durumunda en ucuz olan tekliftir."
                )
            else:
                reason = "Seçilen teklif kriterlerinize en uygun olanıdır."

            def simplify_offer(offer, qty):
                return {
                    "id": offer.id,
                    "seller_name": offer.seller.name,
                    "unit_price": offer.price,
                    "shipping_cost": offer.shippingFee,
                    "total_cost": offer.price * qty + offer.shippingFee,
                    "delivery_time_days": offer.deliveryTimeDays,
                    "seller_rating": offer.seller.rating
                }

            result_data = {
                "success": True,
                "product_id": product_id,
                "quantity": quantity,
                "requested_quantity": quantity,
                "selected_offer": simplify_offer(selected_offer, quantity),
                "alternatives_count": len(alternatives)
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result_data, ensure_ascii=False)
                )
            ]

        elif name == "create_procurement_plan":
            items = arguments.get("items")
            objective = arguments.get("objective", "CHEAPEST")
            valid_objectives = {
                "CHEAPEST",
                "FASTEST",
                "HIGHEST_RATED",
                "BALANCED"
            }

            if objective not in valid_objectives:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "success": False,
                                "error": f"Invalid objective: {objective}"
                            },
                            ensure_ascii=False
                        )
                    )
                ]
            filters = arguments.get("filters") or {}

            if not items:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No items provided."}, ensure_ascii=False)
                    )
                ]

            plan_items = []
            overall_total = 0.0
            complete_overall = True

            for item in items:
                product_id = item.get("product_id")
                quantity = item.get("quantity")
                if not product_id or not quantity:
                    continue

                try:
                    offers = await service.get_offers_by_product_id(product_id)
                except Exception as e:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "success": False,
                                    "error": (
                                        f"Error fetching offers for product "
                                        f"{product_id}: {str(e)}"
                                    )
                                },
                                ensure_ascii=False
                            )
                        )
                    ]

                product_name = "Bilinmeyen Ürün"

                if offers:
                    product_name = offers[0].product.name

                offers_list = []

                for o in offers:
                    offers_list.append({
                        "offer_id": o.id,
                        "available_stock": o.stockQuantity,
                        "unit_price": o.price,
                        "seller_name": o.seller.name,
                        "rating": o.seller.rating,
                        "delivery_days": o.deliveryTimeDays,
                        "shipping_cost": o.shippingFee,
                        "topsis_score": getattr(o, "topsisScore", 0.0)
                    })

                logger.info(
                    "[PROCUREMENT] Product ID: %s, Requested quantity: %s, Objective: %s",
                    product_id, quantity, objective,
                )

                for offer in offers_list:
                    logger.debug(
                        "[OFFER] Seller=%s, Stock=%s, Price=%s",
                        offer["seller_name"], offer["available_stock"], offer["unit_price"],
                    )

                # 2. Run greedy allocation across offers
                alloc_res = allocate_across_offers(
                    offers_list,
                    quantity,
                    objective,
                    filters
                )

                if not alloc_res["complete"]:
                    complete_overall = False

                plan_items.append({
                    "product_id": product_id,
                    "product_name": product_name,
                    "required_quantity": quantity,
                    "fulfilled_quantity": alloc_res["fulfilled_quantity"],
                    "missing_quantity": alloc_res["missing_quantity"],
                    "complete": alloc_res["complete"],
                    "allocations": alloc_res["allocations"],
                    "total_cost": alloc_res["overall_total"]
                })
                overall_total += alloc_res["overall_total"]

            result = {
                "success": True,
                "complete": complete_overall,
                "items": plan_items,
                "overall_total": overall_total
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "check_availability":
            offer_id = arguments.get("offer_id")
            quantity = arguments.get("quantity")
            if not offer_id or not quantity:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No offer ID or quantity provided."}, ensure_ascii=False)
                    )
                ]
            available = await service.check_availability(offer_id, quantity)
            return [
                TextContent(
                    type="text",
                    text=json.dumps({
                        "success": True,
                        "offer_id": offer_id,
                        "quantity": quantity,
                        "available": available
                    }, ensure_ascii=False)
                )
            ]

        elif name == "create_purchase_draft":
            items = arguments.get("items")
            if not items:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No items provided."}, ensure_ascii=False)
                    )
                ]
            draft = await service.create_purchase_draft(items)
            result = draft.model_dump()
            result["success"] = True
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "place_order":
            draft_id = arguments.get("draft_id")
            if not draft_id:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No draft ID provided."}, ensure_ascii=False)
                    )
                ]
            try:
                order = await service.place_order(draft_id)
                result = order.model_dump()
                result["success"] = True
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False)
                    )
                ]
            except Exception as e:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": f"Error placing order: {str(e)}"}, ensure_ascii=False)
                    )
                ]

        elif name == "get_order_status":
            order_id = arguments.get("order_id")
            if not order_id:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": "No order ID provided."}, ensure_ascii=False)
                    )
                ]
            try:
                status = await service.get_order_details(order_id)
                if not status:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"success": False, "error": f"Order with ID {order_id} not found."}, ensure_ascii=False)
                        )
                    ]
                result = status.model_dump()
                result["success"] = True
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False)
                    )
                ]
            except Exception as e:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": False, "error": f"Error getting order status: {str(e)}"}, ensure_ascii=False)
                    )
                ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=json.dumps({"success": False, "error": f"Error executing tool: {str(e)}"}, ensure_ascii=False)
            )
        ]
    return [
        TextContent(
            type="text",
            text=json.dumps({"success": False, "error": "Invalid tool name."}, ensure_ascii=False)
        )
    ]

if __name__ == "__main__":
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions
    from mcp.server.lowlevel import NotificationOptions

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="marketplace-server",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(main())

            