from mcp.server import Server
from mcp.types import Tool, TextContent
from services import ProductService
import asyncio
import json

# Initialize Server and Service
server = Server("stock-server")
service = ProductService()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="list_products",
            description="List all products with their SKU, stock levels, and warehouse location.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        Tool(
            name="search_products",
            description="Search for products by name, SKU, category, subcategory, or description.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text."
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="list_out_of_stock",
            description="List all products which are currently completely out of stock (quantity = 0)",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        Tool(
            name="list_low_stock",
            description="List the products whose stock levels are at or below their defined minimum thresholds (excluding out-of-stock).",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        Tool(
            name="calculate_replenishment",
            description="Calculate replenishment quantities for products based on current stock levels and pending incoming orders. Can optionally filter by category name, product ID, or a list of product IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category name to filter the products (case-insensitive)."
                    },
                    "product_id": {
                        "type": "integer",
                        "description": "Optional product ID to calculate replenishment for a specific product."
                    },
                    "product_ids": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "integer"}},
                            {"type": "integer"}
                        ],
                        "description": "Optional list of product IDs or a single product ID."
                    }
                },
                "additionalProperties": False,
            }
        ),
        Tool(
            name="create_incoming_order",
            description="Create a pending incoming order for stock replenishment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "ID of the product to order."
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Quantity to order."
                    },
                    "expected_delivery_date": {
                        "type": "string",
                        "description": "Expected delivery date (ISO-8601 string, optional)."
                    }
                },
                "required": ["product_id", "quantity"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="create_incoming_orders",
            description=(
                "Register multiple purchased products as pending incoming stock in a single call. "
                "Use this right after place_order to record the ordered items as expected stock, "
                "so replenishment calculations stop requesting them again."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Products to register as incoming stock.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {
                                    "type": "integer",
                                    "description": "ID of the product."
                                },
                                "quantity": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Ordered quantity."
                                },
                                "expected_delivery_date": {
                                    "type": "string",
                                    "description": ("Expected delivery date, ISO-8601. "
                                                    "Prefer the full value returned by place_order "
                                                    "(e.g. 2026-08-23T10:27:15); a plain date also works.")
                                }
                            },
                            "required": ["product_id", "quantity"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="receive_order",
            description="Receive a pending incoming order, adding its quantity to current stock level.",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "ID of the pending order to receive."
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            }
        ),
        Tool(
            name="get_stock_replenishment_needed",
            description="Get a list of all products that need stock replenishment, with calculated order quantities. Can optionally filter by product ID or a list of product IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "Optional product ID to get replenishment details for a specific product."
                    },
                    "product_ids": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "integer"}},
                            {"type": "integer"}
                        ],
                        "description": "Optional list of product IDs or a single product ID."
                    }
                },
                "additionalProperties": False,
            }
        )
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Execute the tool selected by LLM using ProductService"""
    arguments = arguments or {}
    try:
        if name == "list_products":
            products = await service.get_all_products()
            result = {
                "success": True,
                "count": len(products),
                "products": [p.model_dump() for p in products]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "search_products":
            query = arguments.get("query", "")
            products = await service.search_products(query)
            result = {
                "success": True,
                "query": query,
                "count": len(products),
                "products": [p.model_dump() for p in products]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "list_out_of_stock":
            products = await service.get_out_of_stock_products()
            result = {
                "success": True,
                "count": len(products),
                "products": [p.model_dump() for p in products]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "list_low_stock":
            products = await service.get_low_stock_products()
            result = {
                "success": True,
                "count": len(products),
                "products": [p.model_dump() for p in products]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name in ("calculate_replenishment", "get_stock_replenishment_needed"):
            product_id = arguments.get("product_id")
            product_ids = arguments.get("product_ids")
            replenishments = await service.calculate_replenishment(product_id, product_ids)
            category = arguments.get("category")
            if category:
                replenishments = [
                    r for r in replenishments
                    if r.categoryName and r.categoryName.casefold() == category.casefold()
                ]
            result = {
                "success": True,
                "count": len(replenishments),
                "replenishments": [r.model_dump() for r in replenishments]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "create_incoming_order":
            product_id = arguments.get("product_id")
            quantity = arguments.get("quantity")
            expected_delivery_date = arguments.get("expected_delivery_date")
            order = await service.create_incoming_order(product_id, quantity, expected_delivery_date)
            if order:
                result = {
                    "success": True,
                    "order": order.model_dump()
                }
            else:
                result = {
                    "success": False,
                    "error": "Failed to create incoming order"
                }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "create_incoming_orders":
            items = arguments.get("items")
            if not items:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "success": False,
                            "error": (
                                "No items provided. Call place_order first and pass its result via "
                                "$from with the 'order_to_incoming_items' transform."
                            )
                        }, ensure_ascii=False)
                    )
                ]

            orders = await service.create_incoming_orders(items)
            result = {
                "success": True,
                "count": len(orders),
                "orders": [o.model_dump() for o in orders]
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        elif name == "receive_order":
            order_id = arguments.get("order_id")
            order = await service.receive_order(order_id)
            if order:
                result = {
                    "success": True,
                    "order": order.model_dump()
                }
            else:
                result = {
                    "success": False,
                    "error": "Failed to receive order."
                }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False)
                )
            ]

        else:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"success": False, "error": f"Unknown tool name: {name}"}, ensure_ascii=False)
                )
            ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=json.dumps({"success": False, "error": f"Error executing tool {name}: {str(e)}"}, ensure_ascii=False)
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
                    server_name="stock-server",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(main())
