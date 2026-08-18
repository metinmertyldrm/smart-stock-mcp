"""Testler için ortak yardımcılar.

`mcp` ve `fastapi` kurulu değilse (ör. sade bir CI konteyneri) asgari sahte
modüller kurar; böylece testler hem geliştirici makinesinde hem de bağımlılık
yüklenmemiş ortamlarda çalışır.
"""
import importlib.machinery
import json
import sys
import types


def install_optional_stubs():
    """Eksik olan opsiyonel bağımlılıkların yerine asgari sahte modül koyar."""
    _stub_mcp()
    _stub_fastapi()


def _register(modules):
    """Sahte modülleri kaydeder.

    __spec__ atamak şart: importlib.util.find_spec(), __spec__'i None olan bir
    modül için ValueError fırlatır ve bazı testler varlık kontrolünü onunla yapar.
    """
    for name, module in modules.items():
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        sys.modules[name] = module


def _stub_mcp():
    try:
        import mcp.client.stdio  # noqa: F401
        import mcp.types  # noqa: F401
        return
    except Exception:
        pass

    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = object
    mcp_mod.StdioServerParameters = object

    client_mod = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = None

    types_mod = types.ModuleType("mcp.types")
    types_mod.TextContent = type("TextContent", (), {})
    types_mod.Tool = type("Tool", (), {})

    server_mod = types.ModuleType("mcp.server")
    server_mod.Server = type("Server", (), {})

    _register({
        "mcp": mcp_mod,
        "mcp.client": client_mod,
        "mcp.client.stdio": stdio_mod,
        "mcp.types": types_mod,
        "mcp.server": server_mod,
    })


def _stub_fastapi():
    try:
        import fastapi  # noqa: F401
        return
    except Exception:
        pass

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class FastAPI:
        def __init__(self, **kwargs):
            self.state = types.SimpleNamespace()

        def add_middleware(self, *args, **kwargs):
            return None

        def _decorator(self, *args, **kwargs):
            return lambda func: func

        get = post = delete = _decorator

    fastapi_mod = types.ModuleType("fastapi")
    fastapi_mod.FastAPI = FastAPI
    fastapi_mod.HTTPException = HTTPException
    fastapi_mod.Response = object
    fastapi_mod.Header = lambda *args, **kwargs: None

    middleware_mod = types.ModuleType("fastapi.middleware")
    cors_mod = types.ModuleType("fastapi.middleware.cors")
    cors_mod.CORSMiddleware = object

    _register({
        "fastapi": fastapi_mod,
        "fastapi.middleware": middleware_mod,
        "fastapi.middleware.cors": cors_mod,
    })


class FakeToolResult:
    """MCP sunucusunun TextContent cevabını taklit eder."""

    def __init__(self, payload):
        text = json.dumps(payload, ensure_ascii=False)
        self.content = [types.SimpleNamespace(text=text)]
        self.isError = False


class FakeTool:
    inputSchema = {"type": "object", "properties": {}}

    def __init__(self, name):
        self.name = name
        self.description = "test tool"


class FakeMCPClient:
    """Gerçek MCP sunucusu yerine, adı verilen tool'lara sabit cevap döndürür."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def list_tools(self):
        return [FakeTool(name) for name in self.responses]

    async def call_tool(self, name, arguments=None):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        handler = self.responses[name]
        payload = handler(arguments) if callable(handler) else handler
        return FakeToolResult(payload)

    @property
    def called_tools(self):
        return [name for name, _ in self.calls]


class ScriptedLLM:
    """Sırayla verilen cevapları döndürür; son cevap tükenirse tekrarlanır."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append(messages)
        index = min(len(self.prompts) - 1, len(self.outputs) - 1)
        return self.outputs[index]

    @property
    def call_count(self):
        return len(self.prompts)


def procurement_plan(overall_total, delivery_days, ratings):
    """create_procurement_plan çıktısına benzer bir sonuç üretir."""
    items = []
    for index, (days, rating) in enumerate(zip(delivery_days, ratings), start=1):
        items.append({
            "product_id": index,
            "product_name": f"Ürün {index}",
            "required_quantity": 5,
            "fulfilled_quantity": 5,
            "missing_quantity": 0,
            "complete": True,
            "total_cost": overall_total / max(1, len(delivery_days)),
            "allocations": [{
                "offer_id": index,
                "seller_name": "Satıcı",
                "quantity": 5,
                "unit_price": 1.0,
                "shipping_cost": 0.0,
                "subtotal": 1.0,
                "rating": rating,
                "delivery_days": days,
            }],
        })
    return {"success": True, "complete": True, "items": items, "overall_total": overall_total}


PLACED_ORDER = {
    "success": True,
    "id": 77,
    "draftId": 12,
    "totalCost": 41250.0,
    "status": "PENDING",
    "createdAt": "2026-08-18T09:15:00",
    "expectedDeliveryDate": "2026-08-21T09:15:00",
    "items": [
        {
            "id": 1,
            "product": {"id": 3, "sku": "SKU-3", "name": "Galaxy S24",
                        "stockQuantity": 0, "minimumStock": 5, "targetStock": 8},
            "quantity": 8,
            "seller": {"id": 1, "name": "ElectroShop", "rating": 4.6, "baseDeliveryDays": 2},
            "price": 5000.0, "shippingFee": 250.0, "deliveryTimeDays": 3,
        },
        {
            "id": 2,
            "product": {"id": 9, "sku": "SKU-9", "name": "A4 Kağıt",
                        "stockQuantity": 2, "minimumStock": 10, "targetStock": 32},
            "quantity": 30,
            "seller": {"id": 2, "name": "BudgetMarket", "rating": 4.9, "baseDeliveryDays": 1},
            "price": 40.0, "shippingFee": 0.0, "deliveryTimeDays": 1,
        },
    ],
}
