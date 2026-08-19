import os
import json
import re
import asyncio
from mcp_client import MCPClient
from llm import LLMService
from prompt import get_execution_plan_prompt, get_reasoning_prompt

# Configure paths relative to this script
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_SERVER_PATH = os.path.join(BASE_DIR, "stock-mcp", "tools.py")
MARKETPLACE_SERVER_PATH = os.path.join(BASE_DIR, "marketplace-mcp", "tools.py")


INFO_TOOLS = {
    "list_out_of_stock",
    "list_low_stock",
    "search_products",
    "list_products",
    "compare_offers",
    "search_offers",
    "list_sellers",
    "get_order_status",
    "list_incoming_orders",
    "list_marketplace_orders",
}


def remove_json_comments(text: str) -> str:
    # Safely strip JavaScript-style single-line and multi-line comments from JSON string
    comment_pattern = r'("(?:\\.|[^"\\])*")|(?:\/\*(?:[^*]|\*(?!\/))*\*\/)|(?:\/\/.*)'
    def replace(match):
        if match.group(1):
            return match.group(1)
        return ""
    return re.sub(comment_pattern, replace, text)


def parse_execution_plan(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    start_index = cleaned.find("{")
    if start_index == -1:
        raise ValueError("LLM cevabında JSON bulunamadı.")

    json_str = remove_json_comments(cleaned[start_index:])
    
    try:
        decoder = json.JSONDecoder(strict=False)
        parsed, _ = decoder.raw_decode(json_str)
    except Exception as parse_err:
        repaired_str = json_str
        import re
        pattern = r'"(final_response|answer)"\s*:\s*"(.*)"\s*\}\s*[^}]*$'
        match = re.search(pattern, json_str, re.DOTALL)
        if match:
            key = match.group(1)
            val = match.group(2)
            escaped_val = json.dumps(val)
            start_idx = match.start()
            repaired_str = json_str[:start_idx] + f'"{key}": {escaped_val}\n}}'
        
        try:
            decoder = json.JSONDecoder(strict=False)
            parsed, _ = decoder.raw_decode(repaired_str)
        except Exception:
            for key in ['"final_response"', '"answer"']:
                idx = json_str.find(key)
                if idx != -1:
                    comma_idx = json_str.rfind(",", 0, idx)
                    if comma_idx != -1:
                        tmp_str = json_str[:comma_idx].strip() + "\n}"
                        try:
                            decoder = json.JSONDecoder(strict=False)
                            parsed, _ = decoder.raw_decode(tmp_str)
                            repaired_str = tmp_str
                            break
                        except Exception:
                            pass
            else:
                raise parse_err

    if parsed.get("type") != "execution_plan":
        raise ValueError("Beklenen response type: execution_plan")

    goal = parsed.get("goal", "").upper()
    
    # CHAT goal must not have steps and must have answer
    if goal == "CHAT":
        if not parsed.get("answer"):
            raise ValueError("CHAT planı için 'answer' alanı bulunmalıdır.")
        if parsed.get("steps"):
            raise ValueError("CHAT planı adımlar (steps) içeremez.")
        return parsed

    parsed.setdefault("steps", [])
    steps = parsed["steps"]

    # REASON plan allows empty steps if valid context_sources are present
    if not steps:
        if goal == "REASON" and parsed.get("context_sources"):
            pass
        else:
            raise ValueError("Execution plan içinde steps bulunmalıdır.")

    # Validate context_sources
    context_sources = parsed.get("context_sources")
    if context_sources is not None:
        if not isinstance(context_sources, list):
            raise ValueError("context_sources list tipinde olmalıdır.")
        for src in context_sources:
            if src not in ALLOWED_CONTEXT_SOURCES:
                raise ValueError(f"Geçersiz context source: {src}")

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] object olmalıdır.")
        if not step.get("tool"):
            raise ValueError(f"steps[{index}] içinde tool eksik.")
        if "params" in step:
            raise ValueError(f"steps[{index}] 'params' içeremez, 'arguments' kullanılmalıdır.")
        if "arguments" in step and not isinstance(step.get("arguments"), dict):
            raise ValueError(f"steps[{index}].arguments object olmalıdır.")

    WRITE_TOOLS = {
        "create_purchase_draft",
        "place_order",
        "create_incoming_order",
        "create_incoming_orders",
        "receive_order",
        "receive_orders",
    }
    INFO_TOOLS = {
        "list_out_of_stock",
        "list_low_stock",
        "search_products",
        "list_products",
        "compare_offers",
        "search_offers",
        "list_sellers",
        "get_order_status",
        "list_incoming_orders",
        "list_marketplace_orders",
    }

    if goal == "INFO":
        allowed_info_tools = INFO_TOOLS | {"calculate_replenishment", "get_stock_replenishment_needed"}
        for step in steps:
            if step["tool"] not in allowed_info_tools:
                raise ValueError(f"INFO planı sadece salt okunur araçlar içerebilir. Geçersiz araç: {step['tool']}")

    elif goal == "PLAN":
        if not steps or steps[-1]["tool"] != "create_procurement_plan":
            raise ValueError("PLAN planının son adımı 'create_procurement_plan' olmalıdır.")

    elif goal == "DRAFT":
        if not steps or steps[-1]["tool"] != "create_purchase_draft":
            raise ValueError("DRAFT planının son adımı 'create_purchase_draft' olmalıdır.")
        for step in steps:
            if step["tool"] == "place_order":
                raise ValueError("DRAFT planı 'place_order' adımı içeremez.")

    elif goal == "ORDER":
        if not steps:
            raise ValueError("ORDER planında en az bir adım bulunmalıdır.")
        if not any(step["tool"] == "place_order" for step in steps):
            raise ValueError("ORDER planı 'place_order' adımı içermelidir.")
        if steps[-1]["tool"] not in ("place_order", "create_incoming_orders"):
            raise ValueError(
                "ORDER planının son adımı 'place_order' veya 'create_incoming_orders' olmalıdır."
            )

    elif goal == "RECEIVE":
        # Teslim alma hem okuma hem yazma icerir; INFO salt okunur oldugu icin
        # bu akisin kendi hedefi var. Iki bicimi olabilir:
        #   1) oneri  : yalnizca listeleme -> host kullaniciya onay sorar
        #   2) uygulama: onaydan sonra receive_orders
        if not steps:
            raise ValueError("RECEIVE planında en az bir adım bulunmalıdır.")
        allowed = {"list_incoming_orders", "list_marketplace_orders", "get_order_status",
                   "receive_order", "receive_orders"}
        for step in steps:
            if step["tool"] not in allowed:
                raise ValueError(
                    f"RECEIVE planı yalnızca teslim alma araçlarını içerebilir. "
                    f"Geçersiz araç: {step['tool']}"
                )

    elif goal == "REASON":
        for step in steps:
            if step["tool"] in WRITE_TOOLS:
                raise ValueError("REASON planı yazma araçları (write tools) içeremez.")

    return parsed


def validate_plan_against_state(plan: dict, state) -> None:
    """Sozdizimsel olarak gecerli ama mevcut duruma aykiri planlari reddeder.

    Modelin kurallara uymasina guvenmek yerine host tarafinda dogruluyoruz;
    ozellikle siparis verme gibi geri alinamaz adimlarda bu bir guvenlik siniri.
    """
    goal = (plan.get("goal") or "").upper()
    steps = plan.get("steps") or []

    wants_order = goal == "ORDER" or any(
        step.get("tool") == "place_order" for step in steps if isinstance(step, dict)
    )
    wants_receive = any(
        step.get("tool") in ("receive_order", "receive_orders")
        for step in steps if isinstance(step, dict)
    )
    if wants_receive:
        pending_receive = getattr(state, "pending_receive_ids", None) if state is not None else None
        if not pending_receive:
            raise ValueError(
                "Onay bekleyen teslim alma yok, bu yuzden stok artirilamaz. "
                "Once list_incoming_orders ile bekleyen siparisleri listele; "
                "kullanici onayladiktan sonra receive_orders cagrilabilir."
            )

    if wants_order:
        pending = getattr(state, "pending_draft_id", None) if state is not None else None
        if not pending:
            raise ValueError(
                "Onay bekleyen bir taslak yok, bu yuzden place_order calistirilamaz. "
                "Once ihtiyaci hesapla (calculate_replenishment), create_procurement_plan "
                "ile plani kur ve create_purchase_draft ile taslagi olustur; goal DRAFT olmali."
            )


def replenishments_to_items(value):
    items = []
    for replenishment in value or []:
        product_id = replenishment.get("productId") or replenishment.get("product_id")
        quantity = replenishment.get("replenishmentQuantityNeeded") or replenishment.get("quantity")
        if product_id is None or quantity is None:
            continue
        if quantity <= 0:
            continue
        items.append({
            "product_id": int(product_id),
            "quantity": int(quantity),
        })
    return items


def out_of_stock_products_to_items(products):
    items = []
    if isinstance(products, dict):
        products = products.get("products") or []
    for product in products or []:
        product_id = product.get("id") or product.get("productId")
        stock = product.get("stockQuantity", 0)
        target = product.get("targetStock")

        if product_id is None or target is None:
            continue

        quantity = int(target) - int(stock)

        if quantity <= 0:
            continue

        items.append({
            "product_id": int(product_id),
            "quantity": quantity
        })

    return items


def low_stock_products_to_items(products):
    items = []
    if isinstance(products, dict):
        products = products.get("products") or []
    for product in products or []:
        product_id = product.get("id") or product.get("productId")
        stock = product.get("stockQuantity", 0)
        target = product.get("targetStock")

        if product_id is None or target is None:
            continue

        quantity = int(target) - int(stock)

        if quantity <= 0:
            continue

        items.append({
            "product_id": int(product_id),
            "quantity": quantity
        })

    return items


def plan_to_draft_items(value):
    if isinstance(value, dict):
        if "result" in value and isinstance(value["result"], dict) and "items" in value["result"]:
            value = value["result"].get("items")
        elif "items" in value:
            value = value["items"]
    elif hasattr(value, "result") and isinstance(value.result, dict) and "items" in value.result:
        value = value.result.get("items")
    elif hasattr(value, "items") and not callable(value.items):
        value = value.items

    draft_items = []
    if isinstance(value, list):
        for item in value or []:
            allocations = item.get("allocations")
            if allocations:
                for allocation in allocations:
                    offer_id = allocation.get("offer_id") or allocation.get("id")
                    quantity = allocation.get("quantity")
                    if offer_id is not None and quantity is not None and quantity > 0:
                        draft_items.append({
                            "offer_id": int(offer_id),
                            "quantity": int(quantity),
                        })
            else:
                offer_id = item.get("offer_id") or item.get("id")
                quantity = item.get("quantity")
                if offer_id is not None and quantity is not None and quantity > 0:
                    draft_items.append({
                        "offer_id": int(offer_id),
                        "quantity": int(quantity),
                    })
    elif isinstance(value, dict):
        allocations = value.get("allocations")
        if allocations:
            for allocation in allocations:
                offer_id = allocation.get("offer_id") or allocation.get("id")
                quantity = allocation.get("quantity")
                if offer_id is not None and quantity is not None and quantity > 0:
                    draft_items.append({
                        "offer_id": int(offer_id),
                        "quantity": int(quantity),
                    })
        else:
            selected_offer = value.get("selected_offer")
            if selected_offer:
                offer_id = selected_offer.get("offer_id") or selected_offer.get("id")
                quantity = value.get("requested_quantity") or value.get("quantity") or selected_offer.get("quantity") or 1
                if offer_id is not None and quantity is not None and quantity > 0:
                    draft_items.append({
                        "offer_id": int(offer_id),
                        "quantity": int(quantity),
                    })
            else:
                offer_id = value.get("offer_id") or value.get("id")
                quantity = value.get("quantity") or 1
                if offer_id is not None:
                    draft_items.append({
                        "offer_id": int(offer_id),
                        "quantity": int(quantity),
                    })
    return draft_items



def order_to_incoming_items(value):
    """place_order ciktisini create_incoming_orders girdisine cevirir."""
    if not isinstance(value, dict):
        raise ValueError("order_to_incoming_items yalnizca place_order sonucu kabul eder.")

    items = value.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Siparis icinde kalem bulunamadi.")

    # Backend CreateOrderRequestDto.expectedDeliveryDate bir LocalDateTime; saat
    # kismini atarsak Jackson ayristiramiyor ve /api/orders 400 donuyor.
    # Siparisin verdigi degeri oldugu gibi tasiyoruz.
    expected = value.get("expectedDeliveryDate") or value.get("expected_delivery_date")
    expected_date = expected if isinstance(expected, str) and expected else None

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        product = item.get("product") if isinstance(item.get("product"), dict) else {}
        product_id = product.get("id") or item.get("product_id") or item.get("productId")
        quantity = item.get("quantity")
        if product_id is None or not quantity:
            continue
        entry = {"product_id": int(product_id), "quantity": int(quantity)}
        if expected_date:
            entry["expected_delivery_date"] = expected_date
        result.append(entry)

    if not result:
        raise ValueError("Siparis kalemlerinden gecerli urun/miktar cikarilamadi.")
    return result


TRANSFORMS = {
    "replenishments_to_items": replenishments_to_items,
    "plan_to_draft_items": plan_to_draft_items,
    "out_of_stock_products_to_items": out_of_stock_products_to_items,
    "low_stock_products_to_items": low_stock_products_to_items,
    "order_to_incoming_items": order_to_incoming_items,
}


def get_nested_value(data: dict, path: str):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            val = current.get(part)
            if val is None and part in {"id", "productId", "product_id"}:
                val = current.get("productId") or current.get("product_id") or current.get("id")
            current = val
        elif isinstance(current, list):
            if part.isdigit():
                idx = int(part)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                projected = []
                for item in current:
                    if isinstance(item, dict):
                        val = item.get(part)
                        if val is None and part in {"id", "productId", "product_id"}:
                            val = item.get("productId") or item.get("product_id") or item.get("id")
                        if val is not None:
                            projected.append(val)
                    elif hasattr(item, part):
                        val = getattr(item, part)
                        if val is not None:
                            projected.append(val)
                current = projected
        else:
            return None
    return current


from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

@dataclass
class CachedProcurementPlan:
    objective: str
    items: list
    result: dict
    saved_at: str

@dataclass
class ConversationState:
    last_plan: dict | None = None
    last_cheapest_plan: Any = None
    last_fastest_plan: Any = None
    last_product: dict | None = None
    last_replenishment: dict | None = None
    references: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_reference_id: str | None = None
    last_user_message: str | None = None
    pending_draft_id: int | None = None
    # Stok degistiren teslim alma da onay bekler (taslak: "stok degistirme
    # islemlerinden once kullanici onayi alacaktir").
    pending_receive_ids: list[int] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)

ALLOWED_CONTEXT_SOURCES = {
    "last_plan",
    "last_cheapest_plan",
    "last_fastest_plan",
    "last_product",
    "last_replenishment",
    "last_reference",
    "pending_draft_id",
    "pending_receive_ids"
}

def is_plan_valid(plan) -> bool:
    if plan is None:
        return False
    if isinstance(plan, dict):
        if "replenishment_quantity_needed" in plan or "replenishmentQuantityNeeded" in plan:
            return True
        return plan.get("success") is True
    if hasattr(plan, "saved_at"):
        try:
            saved_dt = datetime.fromisoformat(plan.saved_at)
            if saved_dt.tzinfo is None:
                saved_dt = saved_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if (now - saved_dt).total_seconds() > 600:  # 10 minutes
                return False
            res = plan.result
            if isinstance(res, dict) and res.get("success") is False:
                return False
            return True
        except Exception:
            return False
    return False

def normalize_items(items) -> list[tuple[int, int]]:
    if not items:
        return []
    res = []
    for item in items:
        p_id = item.get("product_id") or item.get("productId")
        qty = item.get("quantity") or item.get("replenishmentQuantityNeeded") or item.get("replenishment_quantity_needed")
        if p_id is not None and qty is not None:
            res.append((int(p_id), int(qty)))
    res.sort(key=lambda x: x[0])
    return res

def get_product_id(data: dict) -> int | None:
    if not isinstance(data, dict):
        return None
    val = data.get("productId") or data.get("product_id") or data.get("id")
    return int(val) if val is not None else None

def get_product_name(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    return data.get("productName") or data.get("product_name") or data.get("name")

def get_replenishment_quantity(data: dict) -> int | None:
    if not isinstance(data, dict):
        return None
    val = data.get("replenishmentQuantityNeeded") or data.get("quantity") or data.get("replenishment_quantity_needed")
    return int(val) if val is not None else None

def update_last_product(state: ConversationState, product_id: int, product_name: str):
    if state.last_product and state.last_product.get("id") == product_id:
        state.last_product["name"] = product_name
    else:
        state.last_product = {"id": product_id, "name": product_name}
        state.last_replenishment = None

def item_matches_query(item_name: str, query: str) -> bool:
    if not item_name or not query:
        return False
    query_words = [w.strip(".,!?\"'()").lower() for w in query.split()]
    query_words = [w for w in query_words if len(w) >= 3 and not w.isdigit()]
    
    item_name_lower = item_name.lower()
    for qw in query_words:
        if qw in item_name_lower:
            return True
        item_words = [iw.lower() for iw in item_name.split() if len(iw) >= 3]
        for iw in item_words:
            if iw in qw:
                return True
    return False

def filter_list_by_query(items: list, query: str) -> list:
    if not query or not items:
        return items
    
    matching_items = []
    for item in items:
        name = None
        if isinstance(item, dict):
            name = item.get("productName") or item.get("product_name") or item.get("name") or item.get("title")
        elif hasattr(item, "productName"):
            name = getattr(item, "productName")
        elif hasattr(item, "product_name"):
            name = getattr(item, "product_name")
        elif hasattr(item, "name"):
            name = getattr(item, "name")
            
        if name and item_matches_query(name, query):
            matching_items.append(item)
            
    if matching_items and len(matching_items) < len(items):
        return matching_items
    return items

def resolve_context_path(state: ConversationState, path: str):
    parts = path.split(".")
    first = parts[0]
    
    if first == "last_reference":
        if not state.last_reference_id:
            return None
        ref = state.references.get(state.last_reference_id)
        if not ref:
            return None
        current = ref.get("data")
    else:
        current = getattr(state, first, None)

    # Try to filter current list or nested lists using the last user query keywords
    if state.last_user_message:
        if isinstance(current, list):
            current = filter_list_by_query(current, state.last_user_message)
        elif isinstance(current, dict):
            current = dict(current)
            for list_key in ["items", "products", "replenishments", "offers"]:
                if list_key in current and isinstance(current[list_key], list):
                    current[list_key] = filter_list_by_query(current[list_key], state.last_user_message)
        
    for part in parts[1:]:
        if current is None:
            return None
        if isinstance(current, dict):
            val = current.get(part)
            if val is None:
                # Try to project over a nested list of items/products/replenishments/offers if part not directly in dict
                nested_list = None
                for list_key in ["items", "products", "replenishments", "offers"]:
                    if list_key in current and isinstance(current[list_key], list):
                        nested_list = current[list_key]
                        break
                if nested_list is not None:
                    projected = []
                    for item in nested_list:
                        if isinstance(item, dict):
                            item_val = item.get(part)
                            if item_val is None and part in {"id", "productId", "product_id"}:
                                item_val = item.get("productId") or item.get("product_id") or item.get("id")
                            if item_val is not None:
                                projected.append(item_val)
                        elif hasattr(item, part):
                            item_val = getattr(item, part)
                            if item_val is not None:
                                projected.append(item_val)
                    current = projected
                else:
                    current = None
            else:
                current = val
        elif isinstance(current, list):
            if part.isdigit():
                idx = int(part)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                projected = []
                for item in current:
                    if isinstance(item, dict):
                        val = item.get(part)
                        if val is None and part in {"id", "productId", "product_id"}:
                            val = item.get("productId") or item.get("product_id") or item.get("id")
                        if val is not None:
                            projected.append(val)
                    elif hasattr(item, part):
                        val = getattr(item, part)
                        if val is not None:
                            projected.append(val)
                current = projected
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
            
    if isinstance(current, list) and len(current) == 1:
        if not isinstance(current[0], (dict, list)):
            return current[0]
            
    return current

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_state.json")

def serialize_plan(p):
    """CachedProcurementPlan -> dict. Modul seviyesinde: execute_plan da kullaniyor."""
    if p is None:
        return None
    if isinstance(p, dict):
        return p
    return asdict(p)


def save_state(state: ConversationState):
    try:
        data = {
            "last_plan": state.last_plan,
            "last_cheapest_plan": serialize_plan(state.last_cheapest_plan),
            "last_fastest_plan": serialize_plan(state.last_fastest_plan),
            "last_product": state.last_product,
            "last_replenishment": state.last_replenishment,
            "references": state.references,
            "last_reference_id": state.last_reference_id,
            "last_user_message": state.last_user_message,
            "pending_draft_id": state.pending_draft_id,
            "history": state.history
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Hafıza kaydedilirken hata: {e}")

def load_state() -> ConversationState:
    state = ConversationState()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            state.last_plan = data.get("last_plan")
            
            def deserialize_plan(p_dict):
                if not p_dict:
                    return None
                if "objective" in p_dict and "saved_at" in p_dict:
                    return CachedProcurementPlan(
                        objective=p_dict.get("objective"),
                        items=p_dict.get("items"),
                        result=p_dict.get("result"),
                        saved_at=p_dict.get("saved_at")
                    )
                return p_dict
                
            state.last_cheapest_plan = deserialize_plan(data.get("last_cheapest_plan"))
            state.last_fastest_plan = deserialize_plan(data.get("last_fastest_plan"))
            state.last_product = data.get("last_product")
            state.last_replenishment = data.get("last_replenishment")
            state.references = data.get("references", {})
            state.last_reference_id = data.get("last_reference_id")
            state.last_user_message = data.get("last_user_message")
            state.pending_draft_id = data.get("pending_draft_id")
            state.history = data.get("history", [])
        except Exception as e:
            print(f"Hafıza yüklenirken hata: {e}")
    return state

TRANSFORM_INPUT_TYPES = {
    "out_of_stock_products_to_items": "product_list",
    "low_stock_products_to_items": "product_list",
    "replenishments_to_items": "replenishment_list",
    "plan_to_draft_items": {"procurement_plan", "comparison_response"},
}

def save_reference(
    state: ConversationState,
    reference_type: str,
    source_tool: str,
    data: Any
) -> str:
    ref_id = f"ref_{uuid4().hex[:8]}"
    count = len(data) if isinstance(data, list) else 1
    state.references[ref_id] = {
        "type": reference_type,
        "source_tool": source_tool,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": count,
        "data": data
    }
    state.last_reference_id = ref_id
    return ref_id

def resolve_context_reference(
    value: dict,
    state: ConversationState
):
    source_context = value.get("$from_context")
    source_root = source_context.split(".")[0]
    if source_root not in ALLOWED_CONTEXT_SOURCES:
        raise ValueError(
            f"Desteklenmeyen context referansı: {source_context}"
        )

    if source_context == "last_reference":
        ref_id = state.last_reference_id
        if not ref_id:
            raise ValueError(
                "Bu işlem için kullanılabilecek önceki bir sonuç bulunamadı."
            )
        ref_data = state.references.get(ref_id)
        if not ref_data:
            raise ValueError(
                f"Context referansı bulunamadı: {ref_id}"
            )
        resolved = ref_data["data"]
        
        transform_name = value.get("$transform")
        if transform_name:
            transform = TRANSFORMS.get(transform_name)
            if transform is None:
                raise ValueError(f"Bilinmeyen transform: {transform_name}")

            expected_type = TRANSFORM_INPUT_TYPES.get(transform_name)
            if expected_type:
                allowed = expected_type if isinstance(expected_type, set) else {expected_type}
                if ref_data["type"] not in allowed:
                    raise ValueError(
                        f"{transform_name}, {ref_data['type']} tipinde veri kabul etmiyor."
                    )
            resolved = transform(resolved)
    else:
        resolved = resolve_context_path(state, source_context)
        if resolved is None:
            raise ValueError(
                f"Context referansı bulunamadı veya boş: {source_context}"
            )
        
        transform_name = value.get("$transform")
        if transform_name:
            transform = TRANSFORMS.get(transform_name)
            if transform is None:
                raise ValueError(f"Bilinmeyen transform: {transform_name}")
            resolved = transform(resolved)

    return resolved

def resolve_argument_value(value, execution_results, state=None):
    if not isinstance(value, dict):
        return value

    if "$from_context" in value:
        if state is None:
            raise ValueError("Conversation state context resolution için gerekli.")
        return resolve_context_reference(value, state)

    source = value.get("$from")
    if source:
        step_id, _, result_path = source.partition(".")

        # Model bazen $from_context yerine $from yaziyor ("last_reference.id" gibi).
        # Niyet acik oldugu icin cezalandirmak yerine dogru mekanizmaya yonlendiriyoruz.
        if step_id in ALLOWED_CONTEXT_SOURCES and step_id not in execution_results:
            if state is None:
                raise ValueError(
                    "Conversation state context resolution icin gerekli."
                )
            coerced = dict(value)
            coerced["$from_context"] = coerced.pop("$from")
            return resolve_context_reference(coerced, state)

        step_result = execution_results.get(step_id)
        if step_result is None:
            raise ValueError(f"Kaynak adım sonucu bulunamadı: {step_id}")
        
        if not result_path:
            resolved = step_result
        else:
            resolved = get_nested_value(step_result, result_path)
            # Fallback for compare_offers result referenced as step_id.items
            if (not resolved) and result_path == "items" and isinstance(step_result, dict) and "selected_offer" in step_result:
                resolved = step_result

        if resolved is None and result_path:
            raise ValueError(
                f"Referans çözülemedi: {source} (step sonucu: "
                f"{list(step_result.keys()) if isinstance(step_result, dict) else step_result})"
            )

        transform_name = value.get("$transform")
        if transform_name:
            transform = TRANSFORMS.get(transform_name)
            if transform is None:
                raise ValueError(f"Bilinmeyen transform: {transform_name}")
            resolved = transform(resolved)
        return resolved

    return {
        key: resolve_argument_value(item, execution_results, state)
        for key, item in value.items()
    }


def resolve_step_arguments(arguments, execution_results, state=None):
    return {
        key: resolve_argument_value(value, execution_results, state)
        for key, value in arguments.items()
    }


def extract_result_text(result) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        if texts:
            return "\n".join(texts)
    return str(result)


def normalize_tool_result(result) -> dict:
    if getattr(result, "isError", False):
        return {
            "success": False,
            "error": extract_result_text(result),
        }

    text = extract_result_text(result)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if parsed.get("success") is False:
                return parsed
            return {
                "success": True,
                "data": parsed,
            }
        return {
            "success": True,
            "data": parsed,
        }
    except json.JSONDecodeError:
        lowered = text.lower()
        error_markers = [
            "error", "hata", "invalid", "failed", "required", "no items provided"
        ]
        if any(marker in lowered for marker in error_markers):
            return {
                "success": False,
                "error": text,
            }
        return {
            "success": True,
            "data": {
                "raw_result": text,
            },
        }


def plan_metrics(value):
    """Bir satin alma plani sonucundan olculebilir metrikleri cikarir."""
    if not isinstance(value, dict):
        return None

    objective = value.get("objective")
    plan = value.get("result") if isinstance(value.get("result"), dict) else value
    if not isinstance(plan, dict) or plan.get("overall_total") is None:
        return None

    items = plan.get("items") or []
    delivery_days = []
    ratings = []
    missing = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        missing += item.get("missing_quantity") or 0
        for allocation in item.get("allocations") or []:
            if not isinstance(allocation, dict):
                continue
            if allocation.get("delivery_days") is not None:
                delivery_days.append(allocation["delivery_days"])
            if allocation.get("rating") is not None:
                ratings.append(allocation["rating"])

    metrics = {
        "toplam_maliyet": round(float(plan.get("overall_total") or 0), 2),
        "urun_sayisi": len(items),
        "eksik_kalan_adet": missing,
    }
    if objective:
        metrics["hedef"] = objective
    if delivery_days:
        metrics["en_gec_teslimat_gunu"] = max(delivery_days)
        metrics["en_erken_teslimat_gunu"] = min(delivery_days)
    if ratings:
        metrics["en_dusuk_satici_puani"] = min(ratings)
        metrics["en_yuksek_satici_puani"] = max(ratings)
    return metrics


def build_plan_comparison(results: dict):
    """Planlar arasindaki farklari KODDA hesaplar.

    Reasoning katmanina hazir sayi verilir; 8B model toplama/cikarma yaparken
    hata yapabiliyor ve bu sayilar satin alma kararina girdi oluyor.
    """
    plans = {}
    for key, value in (results or {}).items():
        metrics = plan_metrics(value)
        if metrics:
            plans[key] = metrics

    if not plans:
        return None

    comparison = {"planlar": plans}
    if len(plans) < 2:
        return comparison

    by_cost = sorted(plans.items(), key=lambda kv: kv[1]["toplam_maliyet"])
    ucuz_key, ucuz = by_cost[0]
    pahali_key, pahali = by_cost[-1]
    cost_gap = round(pahali["toplam_maliyet"] - ucuz["toplam_maliyet"], 2)

    fark = {
        "daha_ucuz_olan": ucuz_key,
        "daha_pahali_olan": pahali_key,
        "maliyet_farki_TL": cost_gap,
    }
    if ucuz["toplam_maliyet"]:
        fark["maliyet_farki_yuzde"] = round(100 * cost_gap / ucuz["toplam_maliyet"], 1)

    timed = {k: v for k, v in plans.items() if "en_gec_teslimat_gunu" in v}
    if len(timed) >= 2:
        hizli_key, hizli = min(timed.items(), key=lambda kv: kv[1]["en_gec_teslimat_gunu"])
        yavas_key, yavas = max(timed.items(), key=lambda kv: kv[1]["en_gec_teslimat_gunu"])
        fark["daha_hizli_olan"] = hizli_key
        fark["teslimat_farki_gun"] = yavas["en_gec_teslimat_gunu"] - hizli["en_gec_teslimat_gunu"]

    comparison["fark"] = fark
    return comparison


def clean_tool_results_for_reasoning(results: dict) -> dict:
    cleaned = {}
    for step_id, step_res in results.items():
        if not step_res:
            continue
        if isinstance(step_res, dict):
            data = step_res.get("data") if "data" in step_res else step_res
            if isinstance(data, dict):
                compact_data = {k: v for k, v in data.items() if k not in {"description", "raw_result"}}
                cleaned[step_id] = compact_data
            else:
                cleaned[step_id] = data
        else:
            cleaned[step_id] = step_res

    comparison = build_plan_comparison(cleaned)
    if comparison:
        cleaned["hesaplanan_karsilastirma"] = comparison
    return cleaned


async def execute_plan(plan: dict, client: MCPClient, available_tool_names: set[str], state=None):
    if plan.get("goal", "").upper() == "CHAT":
        return {
            "success": True,
            "results": {},
            "last_result": {
                "success": True,
                "chat_answer": plan.get("answer", "")
            }
        }

    execution_results = {}
    # REASON plans may intentionally contain no tool calls and use previously
    # cached conversation context instead.  Materialize that context as normal
    # execution results so callers can pass it to the reasoning layer.  This
    # also avoids indexing an empty steps list when the plan completes.
    declared_sources = plan.get("context_sources") or []
    for source in declared_sources:
        value = getattr(state, source, None) if state is not None else None
        if value is not None:
            execution_results[source] = serialize_plan(value)

    # Hicbir kaynak cozulemediyse sessizce "basarili" donmek yanlis cevap uretir:
    # reasoning katmani bos veriyle "bilgi yok" der. Basarisiz sayip onarima birakiyoruz.
    if declared_sources and not execution_results:
        return {
            "success": False,
            "failed_step": "context_sources",
            "error": (
                "Plan su baglam kaynaklarini istedi ancak hicbiri mevcut degil: "
                + ", ".join(declared_sources)
                + ". Bu verileri once ilgili tool adimlariyla uret (ornegin "
                "calculate_replenishment ve ardindan create_procurement_plan), "
                "context_sources yerine adim referanslari kullan."
            ),
            "results": execution_results,
        }
    for index, step in enumerate(plan.get("steps", [])):
        step_id = step.get("id") or f"step_{index + 1}"
        tool_name = step["tool"]

        if tool_name not in available_tool_names:
            return {
                "success": False,
                "failed_step": step_id,
                "error": f"Bilinmeyen tool: {tool_name}",
                "results": execution_results,
            }

        try:
            arguments = resolve_step_arguments(
                step.get("arguments", {}),
                execution_results,
                state
            )
            arguments = remove_none_values(arguments)

            # Clean up invalid category argument values for calculate_replenishment
            if tool_name == "calculate_replenishment":
                category = arguments.get("category")
                if isinstance(category, str):
                    invalid_cats = {"kritik", "critical", "azalan", "low stock", "stokta olmayan", "out of stock"}
                    if category.lower().strip() in invalid_cats:
                        arguments.pop("category", None)

            # Category is an inventory filter, never a marketplace argument. Older
            # model plans occasionally leaked it into strict marketplace schemas.
            if tool_name in {"create_procurement_plan", "compare_offers", "search_offers"}:
                arguments.pop("category", None)
                arguments.pop("category_name", None)

            # Date validation
            expected_date_str = arguments.get("expected_delivery_date") or arguments.get("expectedDeliveryDate")
            if expected_date_str:
                from datetime import date
                try:
                    # Java LocalDateTime "2026-08-20T14:30:00" seklinde gelebilir.
                    expected_date = date.fromisoformat(str(expected_date_str).split("T")[0])
                    if expected_date < date.today():
                        raise ValueError("Teslimat tarihi geçmiş bir tarih olamaz.")
                except ValueError as ve:
                    raise ValueError(f"Geçersiz veya geçmiş teslimat tarihi: {expected_date_str}.") from ve

            print(f"[PLAN EXECUTOR] {step_id}: {tool_name}({arguments})")
            
            raw_result = await client.call_tool(tool_name, arguments)
            normalized = normalize_tool_result(raw_result)

            if not normalized.get("success", True):
                return {
                    "success": False,
                    "failed_step": step_id,
                    "failed_tool": tool_name,
                    "error": normalized.get("error") or normalized.get("message") or "Tool başarısız oldu.",
                    "results": execution_results,
                }

            result_data = normalized.get("data") if isinstance(normalized.get("data"), dict) else normalized
            execution_results[step_id] = result_data

            # Save references
            if state is not None:
                if tool_name in {"list_out_of_stock", "list_low_stock", "search_products", "list_products"}:
                    products = result_data.get("products") if isinstance(result_data, dict) else []
                    save_reference(state, "product_list", tool_name, products)
                    if products and isinstance(products, list):
                        p = products[0]
                        p_id = get_product_id(p)
                        p_name = get_product_name(p)
                        if p_id is not None and p_name is not None:
                            update_last_product(state, p_id, p_name)
                elif tool_name in {"calculate_replenishment", "get_stock_replenishment_needed"}:
                    replenishments = result_data.get("replenishments") if isinstance(result_data, dict) else []
                    save_reference(state, "replenishment_list", tool_name, replenishments)
                    if replenishments and isinstance(replenishments, list):
                        rep = replenishments[0]
                        p_id = get_product_id(rep)
                        p_name = get_product_name(rep)
                        qty = get_replenishment_quantity(rep)
                        if p_id is not None:
                            if p_name:
                                update_last_product(state, p_id, p_name)
                            else:
                                current_name = state.last_product.get("name") if state.last_product else "Bilinmeyen Ürün"
                                update_last_product(state, p_id, current_name)
                            state.last_replenishment = {
                                "product_id": p_id,
                                "replenishment_quantity_needed": qty if qty is not None else 0
                            }
                elif tool_name == "create_procurement_plan":
                    save_reference(state, "procurement_plan", tool_name, result_data)
                elif tool_name == "compare_offers":
                    save_reference(state, "comparison_response", tool_name, result_data)

        except Exception as exc:
            return {
                "success": False,
                "failed_step": step_id,
                "failed_tool": tool_name,
                "error": str(exc),
                "results": execution_results,
            }

    steps = plan.get("steps", [])
    if not steps:
        return {
            "success": True,
            "results": execution_results,
            "last_result": execution_results,
        }

    return {
        "success": True,
        "results": execution_results,
        "last_result": (
            execution_results.get(steps[-1].get("id"))
            or execution_results.get(f"step_{len(steps)}")
        ),
    }



# Bos liste her zaman kotu haber degil: kritik urun kalmamasi iyi haberdir.
EMPTY_PRODUCT_MESSAGES = {
    "list_low_stock": "Kritik seviyede ürün yok — tüm stoklar minimum seviyenin üzerinde.",
    "list_out_of_stock": "Stokta tükenmiş ürün yok.",
    "calculate_replenishment": "Şu an sipariş edilmesi gereken ürün yok.",
    "get_stock_replenishment_needed": "Şu an sipariş edilmesi gereken ürün yok.",
    "search_products": "Aramanızla eşleşen ürün bulunamadı.",
}


def format_final_answer(answer, source_tool: str | None = None) -> str:
    if isinstance(answer, dict):
        lines = []
        summary = answer.get("summary") or answer.get("özet")
        if summary:
            lines.append(f"Özet: {summary}\n")
        
        # 1. Handle selected_offer (compare_offers)
        selected_offer = answer.get("selected_offer")
        if selected_offer:
            lines.append("Seçilen Teklif:")
            s_name = selected_offer.get("seller_name")
            u_price = selected_offer.get("unit_price")
            s_cost = selected_offer.get("shipping_cost")
            t_cost = selected_offer.get("total_cost")
            del_days = selected_offer.get("delivery_time_days") or selected_offer.get("delivery_days")
            rating = selected_offer.get("seller_rating") or selected_offer.get("rating")
            
            if s_name:
                lines.append(f"  Satıcı: {s_name}")
            if rating is not None:
                lines.append(f"  Satıcı Puanı: {rating}/5")
            if u_price is not None:
                lines.append(f"  Birim Fiyat: {u_price} TL")
            if s_cost is not None:
                lines.append(f"  Kargo Ücreti: {s_cost} TL")
            if t_cost is not None:
                lines.append(f"  Toplam Maliyet: {t_cost} TL")
            if del_days is not None:
                lines.append(f"  Teslimat Süresi: {del_days} gün")
            
            alt_count = answer.get("alternatives_count")
            if alt_count is not None:
                lines.append(f"\nAlternatif Teklif Sayısı: {alt_count}")

        # 1.5. Handle replenishments list (calculate_replenishment, get_stock_replenishment_needed)
        replenishments = answer.get("replenishments")
        if isinstance(replenishments, list):
            if not replenishments:
                lines.append("Stok yenileme ihtiyacı olan ürün bulunamadı.")
            else:
                categories = {rep.get("categoryName") for rep in replenishments if rep.get("categoryName")}
                if len(categories) == 1:
                    category = list(categories)[0]
                    cat_str = f" {category} kategorisinde"
                else:
                    cat_str = ""
                lines.append(f"{cat_str.strip()} stok yenileme ihtiyacı bulunan {len(replenishments)} ürün bulundu.\n".lstrip())
                for idx, rep in enumerate(replenishments, 1):
                    p_name = rep.get("productName") or rep.get("name") or "Bilinmeyen Ürün"
                    sku = rep.get("sku")
                    stock = rep.get("stockQuantity")
                    min_stock = rep.get("minimumStock")
                    target_stock = rep.get("targetStock")
                    pending = rep.get("pendingIncomingQuantity", 0)
                    needed = rep.get("replenishmentQuantityNeeded") or rep.get("quantity")
                    
                    sku_str = f" (SKU: {sku})" if sku else ""
                    lines.append(f"  {idx}. {p_name}{sku_str}")
                    if stock is not None:
                        lines.append(f"     Mevcut Stok: {stock}")
                    if min_stock is not None:
                        lines.append(f"     Minimum (Kritik) Stok: {min_stock}")
                    if pending is not None:
                        lines.append(f"     Bekleyen Sipariş: {pending}")
                    if target_stock is not None:
                        lines.append(f"     Hedef Stok: {target_stock}")
                    if needed is not None:
                        lines.append(f"     Alınması Gereken Miktar: {needed}")
                    lines.append("")

        # 2. Handle products list (list_products, list_out_of_stock, list_low_stock, search_products)
        products = answer.get("products")
        if isinstance(products, list):
            if not products:
                lines.append(EMPTY_PRODUCT_MESSAGES.get(source_tool, "Ürün bulunamadı."))
            else:
                lines.append("Ürünler:")
                for idx, prod in enumerate(products, 1):
                    p_name = prod.get("name") or prod.get("product_name") or "Bilinmeyen Ürün"
                    sku = prod.get("sku")
                    stock = prod.get("stockQuantity")
                    min_stock = prod.get("minimumStock")
                    target_stock = prod.get("targetStock")
                    wh_info = prod.get("warehouseInfo")
                    
                    sku_str = f" (SKU: {sku})" if sku else ""
                    lines.append(f"  {idx}. {p_name}{sku_str}")
                    if stock is not None:
                        lines.append(f"     Stok Miktarı: {stock}")
                    if min_stock is not None:
                        lines.append(f"     Minimum Stok: {min_stock}")
                    if target_stock is not None:
                        lines.append(f"     Hedef Stok: {target_stock}")
                    if wh_info:
                        lines.append(f"     Depo Bilgisi: {wh_info}")
                    lines.append("")

        # 3. Handle sellers list (list_sellers)
        sellers = answer.get("sellers")
        if isinstance(sellers, list):
            if not sellers:
                lines.append("Satıcı bulunamadı.")
            else:
                lines.append("Satıcılar:")
                for idx, seller in enumerate(sellers, 1):
                    s_name = seller.get("name") or seller.get("seller_name") or "Bilinmeyen Satıcı"
                    rating = seller.get("rating")
                    del_days = seller.get("baseDeliveryDays") or seller.get("delivery_days")
                    
                    lines.append(f"  {idx}. {s_name}")
                    if rating is not None:
                        lines.append(f"     Puan: {rating}/5")
                    if del_days is not None:
                        lines.append(f"     Baz Teslimat Süresi: {del_days} gün")
                    lines.append("")

        # 4. Handle offers list (search_offers)
        offers = answer.get("offers")
        if isinstance(offers, list):
            if not offers:
                lines.append("Teklif bulunamadı.")
            else:
                lines.append("Teklifler:")
                for idx, offer in enumerate(offers, 1):
                    prod = offer.get("product")
                    if isinstance(prod, dict):
                        p_name = prod.get("name") or "Bilinmeyen Ürün"
                        sku = prod.get("sku")
                    else:
                        p_name = offer.get("productName") or "Bilinmeyen Ürün"
                        sku = offer.get("productSku")
                    
                    sku_str = f" (SKU: {sku})" if sku else ""
                    
                    seller_info = offer.get("seller")
                    if isinstance(seller_info, dict):
                        s_name = seller_info.get("name") or "Bilinmeyen Satıcı"
                        rating = seller_info.get("rating")
                    else:
                        s_name = offer.get("seller_name") or "Bilinmeyen Satıcı"
                        rating = offer.get("rating")
                    
                    price = offer.get("price") or offer.get("unit_price")
                    shipping = offer.get("shippingFee") or offer.get("shipping_cost")
                    stock = offer.get("stockQuantity") or offer.get("available_stock")
                    del_days = offer.get("deliveryTimeDays") or offer.get("delivery_days")
                    
                    lines.append(f"  {idx}. {p_name}{sku_str}")
                    lines.append(f"     Satıcı: {s_name}" + (f" (Puan: {rating}/5)" if rating is not None else ""))
                    if price is not None:
                        lines.append(f"     Birim Fiyat: {price} TL")
                    if shipping is not None:
                        lines.append(f"     Kargo Ücreti: {shipping} TL")
                    if stock is not None:
                        lines.append(f"     Stok Miktarı: {stock}")
                    if del_days is not None:
                        lines.append(f"     Teslimat Süresi: {del_days} gün")
                    lines.append("")

        # 5. Handle order status (get_order_status)
        order_status = answer.get("status")
        order_id = answer.get("order_id") or answer.get("id")
        if order_status or order_id or "totalCost" in answer or "draftId" in answer:
            lines.append("Sipariş Bilgileri:")
            if order_id is not None:
                lines.append(f"  Sipariş No: {order_id}")
            draft_id = answer.get("draftId") or answer.get("draft_id")
            if draft_id is not None:
                lines.append(f"  Taslak No: {draft_id}")
            if order_status is not None:
                lines.append(f"  Durum: {order_status}")
            t_cost = answer.get("totalCost") or answer.get("total_cost")
            if t_cost is not None:
                lines.append(f"  Toplam Maliyet: {t_cost} TL")
            del_date = answer.get("expectedDeliveryDate") or answer.get("expected_delivery_date")
            if del_date is not None:
                lines.append(f"  Beklenen Teslimat Tarihi: {del_date}")
            created_at = answer.get("createdAt") or answer.get("created_at")
            if created_at is not None:
                lines.append(f"  Oluşturulma Tarihi: {created_at}")
            lines.append("")

        # 6. Handle items list (plan/draft items)
        items = answer.get("items") or answer.get("ürünler")
        if isinstance(items, list) and items:
            lines.append("Ürünler:")
            for idx, item in enumerate(items, 1):
                prod_val = item.get("product")
                if isinstance(prod_val, dict):
                    p_name = prod_val.get("name") or "Bilinmeyen Ürün"
                    sku = prod_val.get("sku")
                else:
                    p_name = item.get("product_name") or item.get("ürün_adı") or item.get("product") or "Bilinmeyen Ürün"
                    sku = item.get("sku")
                
                qty = item.get("quantity") or item.get("adet") or item.get("quantity_ordered") or item.get("miktar") or item.get("required_quantity")
                
                seller_val = item.get("seller")
                if isinstance(seller_val, dict):
                    seller = seller_val.get("name")
                else:
                    seller = item.get("seller_name") or item.get("satıcı") or item.get("seller")
                
                price = item.get("price") or item.get("unit_price") or item.get("birim_fiyat")
                shipping = item.get("shippingFee") or item.get("shipping_cost") or item.get("kargo") or item.get("shipping")
                total = item.get("total_cost") or item.get("toplam_maliyet") or item.get("total")
                
                del_days = item.get("deliveryTimeDays") or item.get("delivery_days") or item.get("teslimat_süresi") or item.get("delivery_time")
                reason = item.get("selection_reason") or item.get("seçilme_nedeni") or item.get("reason")
                
                sku_str = f" (SKU: {sku})" if sku else ""
                lines.append(f"  {idx}. {p_name}{sku_str}")
                
                allocations = item.get("allocations")
                if allocations:
                    if qty is not None:
                        lines.append(f"     İstenen Miktar: {qty}")
                    lines.append("     Teklif Dağılımı:")
                    for alloc in allocations:
                        s_name = alloc.get("seller_name") or alloc.get("satıcı") or alloc.get("seller")
                        a_qty = alloc.get("quantity") or alloc.get("adet") or alloc.get("miktar")
                        a_price = alloc.get("unit_price") or alloc.get("birim_fiyat") or alloc.get("price")
                        a_shipping = alloc.get("shipping_cost") or alloc.get("kargo")
                        a_subtotal = alloc.get("subtotal") or alloc.get("toplam")
                        
                        lines.append(f"       - Satıcı: {s_name}")
                        lines.append(f"         Miktar: {a_qty}")
                        lines.append(f"         Birim Fiyat: {a_price} TL")
                        if a_shipping is not None:
                            lines.append(f"         Kargo Ücreti: {a_shipping} TL")
                        if a_subtotal is not None:
                            lines.append(f"         Ara Toplam: {a_subtotal} TL")
                else:
                    if qty is not None:
                        lines.append(f"     Miktar: {qty}")
                    if seller:
                        lines.append(f"     Satıcı: {seller}")
                    if price is not None:
                        lines.append(f"     Birim Fiyat: {price}")
                    if shipping is not None:
                        lines.append(f"     Kargo Ücreti: {shipping}")
                        
                if total is not None:
                    lines.append(f"     Toplam Maliyet: {total}")
                if del_days is not None:
                    lines.append(f"     Teslimat Süresi: {del_days} gün")
                if reason:
                    lines.append(f"     Seçilme Nedeni: {reason}")
                lines.append("")
                
        overall = answer.get("overall_total") or answer.get("genel_toplam") or answer.get("total") or answer.get("totalCost")
        if overall is not None:
            lines.append(f"Genel Toplam: {overall} TL")
            
        if not lines:
            try:
                return json.dumps(answer, ensure_ascii=False, indent=2)
            except Exception:
                return str(answer)
        return "\n".join(lines).strip()
    elif isinstance(answer, list):
        lines = []
        for idx, item in enumerate(answer, 1):
            if isinstance(item, dict):
                item_str = ", ".join(f"{k}: {v}" for k, v in item.items())
                lines.append(f"{idx}. {item_str}")
            else:
                lines.append(f"{idx}. {item}")
        return "\n".join(lines)
    else:
        return str(answer)



def format_procurement_plan(result: dict) -> str:
    lines: list[str] = []

    complete = result.get("complete", False)

    if complete:
        lines.append("Satın alma planı başarıyla hazırlandı.")
    else:
        lines.append(
            "Satın alma planı hazırlandı ancak bazı ürünlerin "
            "ihtiyacı tamamen karşılanamadı."
        )

    lines.append("")

    for index, item in enumerate(result.get("items", []), start=1):
        product_name = item.get("product_name", "Bilinmeyen ürün")
        required = item.get(
            "required_quantity",
            item.get("requested_quantity", 0),
        )
        fulfilled = item.get("fulfilled_quantity", 0)
        missing = item.get(
            "missing_quantity",
            max(required - fulfilled, 0),
        )
        total_cost = item.get("total_cost", 0)

        lines.append(f"{index}. {product_name}")
        lines.append(f"   Gerekli miktar: {required}")
        lines.append(f"   Karşılanan miktar: {fulfilled}")
        lines.append(f"   Eksik miktar: {missing}")

        allocations = item.get("allocations", [])

        if not allocations:
            # Tool artik neden tahsis yapilamadigini soyluyor; kullaniciya da aktar.
            reason = item.get("reason")
            if reason:
                lines.append(f"   Uygun teklif bulunamadı: {reason}")
            else:
                lines.append("   Uygun teklif bulunamadı.")
        else:
            lines.append("   Satıcı dağılımı:")

            for allocation in allocations:
                seller = allocation.get(
                    "seller_name",
                    "Bilinmeyen satıcı",
                )
                quantity = allocation.get("quantity", 0)
                unit_price = allocation.get("unit_price", 0)
                shipping = allocation.get("shipping_cost", 0)
                subtotal = allocation.get("subtotal", 0)
                rating = allocation.get("rating")
                delivery_days = allocation.get("delivery_days")

                lines.append(
                    f"   - {seller}: {quantity} adet, "
                    f"birim fiyat {unit_price:,.2f} TL, "
                    f"kargo {shipping:,.2f} TL, "
                    f"ara toplam {subtotal:,.2f} TL"
                )

                details = []

                if rating is not None:
                    details.append(f"puan {rating}")

                if delivery_days is not None:
                    details.append(
                        f"teslimat {delivery_days} gün"
                    )

                if details:
                    lines.append(
                        f"     ({', '.join(details)})"
                    )

        lines.append(
            f"   Ürün toplamı: {total_cost:,.2f} TL"
        )
        lines.append("")

    return "\n".join(lines)


def format_purchase_draft(draft: dict) -> str:
    lines = []
    lines.append("Satın alma taslağı oluşturuldu.\n")
    
    items = draft.get("items") or []
    for item in items:
        prod = item.get("product")
        if isinstance(prod, dict):
            p_name = prod.get("name") or "Bilinmeyen Ürün"
        else:
            p_name = item.get("product_name") or item.get("product") or "Bilinmeyen Ürün"
        qty = item.get("quantity") or 0

        # Ayni urun birden fazla saticiya bolunebiliyor; satici adi olmadan
        # iki satir tekrar gibi gorunuyor.
        seller = item.get("seller")
        seller_name = seller.get("name") if isinstance(seller, dict) else item.get("seller_name")
        price = item.get("price")
        detail = ""
        if seller_name:
            detail = f" — {seller_name}"
            if isinstance(price, (int, float)):
                detail += f", birim {price:,.2f} TL"

        lines.append(f"- {p_name}: {qty} adet{detail}")
        
    t_cost = draft.get("totalCost") or draft.get("total_cost") or 0.0
    lines.append(f"\nToplam tutar: {t_cost:,.2f} TL\n")
    lines.append("Siparişi onaylıyor musunuz?")
    return "\n".join(lines)





def format_receive_proposal(listing: dict) -> str:
    """Bekleyen siparisleri gosterip onay ister (stok degistirmeden once)."""
    orders = (listing or {}).get("orders") or []
    if not orders:
        return "Teslim alınmayı bekleyen sipariş yok."

    lines = ["Teslim alınmayı bekleyen siparişler:", ""]
    for entry in orders:
        if not isinstance(entry, dict):
            continue
        product = entry.get("product")
        name = product.get("name") if isinstance(product, dict) else "Bilinmeyen Ürün"
        expected = (entry.get("expectedDeliveryDate") or "").split("T")[0]
        suffix = f", beklenen teslim {expected}" if expected else ""
        lines.append(f"- #{entry.get('id')} {name}: {entry.get('quantity')} adet{suffix}")

    lines.append("")
    lines.append("Bunları stoğa almamı onaylıyor musunuz?")
    return "\n".join(lines)


def format_received_orders(result: dict) -> str:
    """Teslim alma sonucunu ozetler."""
    orders = (result or {}).get("orders") or []
    if not orders:
        return "Teslim alınan sipariş yok."

    lines = [f"{len(orders)} sipariş teslim alındı ve stoğa eklendi.", ""]
    for entry in orders:
        if not isinstance(entry, dict):
            continue
        product = entry.get("product")
        name = product.get("name") if isinstance(product, dict) else "Bilinmeyen Ürün"
        lines.append(f"- {name}: +{entry.get('quantity')} adet")
    return "\n".join(lines)


def format_order_confirmation(order: dict, incoming: dict | None = None) -> str:
    """place_order + create_incoming_orders zincirini tek cumlelik ozete cevirir.

    Aksi halde kullaniciya ham JSON basiliyordu.
    """
    lines = []

    order_id = order.get("id") if isinstance(order, dict) else None
    total = order.get("totalCost") or order.get("total_cost") if isinstance(order, dict) else None
    header = "Sipariş oluşturuldu"
    if order_id:
        header += f" (#{order_id})"
    if isinstance(total, (int, float)):
        header += f" — toplam {total:,.2f} TL"
    lines.append(header + ".")

    expected = order.get("expectedDeliveryDate") if isinstance(order, dict) else None
    if isinstance(expected, str) and expected:
        lines.append(f"Tahmini teslim tarihi: {expected.split('T')[0]}")

    orders = (incoming or {}).get("orders") if isinstance(incoming, dict) else None
    if orders:
        merged = {}
        for entry in orders:
            if not isinstance(entry, dict):
                continue
            product = entry.get("product")
            name = product.get("name") if isinstance(product, dict) else "Bilinmeyen Ürün"
            merged[name] = merged.get(name, 0) + (entry.get("quantity") or 0)

        lines.append("")
        lines.append("Beklenen stok olarak kaydedildi:")
        for name, quantity in merged.items():
            lines.append(f"- {name}: {quantity} adet")
        lines.append("")
        lines.append("Bu miktarlar ihtiyaç hesabına dahil edildi; "
                     "teslim alındığında stoğa eklenecek.")

    return "\n".join(lines)


def truncate_tool_result_recursive(data, max_items=3):
    if isinstance(data, list):
        return [truncate_tool_result_recursive(x, max_items) for x in data[:max_items]]

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, list):
                result[key] = [
                    truncate_tool_result_recursive(x, max_items)
                    for x in value[:max_items]
                ]
                if len(value) > max_items:
                    result[f"{key}_truncated"] = True
                    result[f"{key}_total_count"] = len(value)
            else:
                result[key] = truncate_tool_result_recursive(value, max_items)
        return result

    return data


def truncate_tool_result(result_str: str, max_chars: int = 5000) -> str:
    try:
        data = json.loads(result_str)
        truncated_data = truncate_tool_result_recursive(data)
        serialized = json.dumps(truncated_data, ensure_ascii=False)
        if len(serialized) <= max_chars:
            return serialized
    except Exception:
        pass
        
    if len(result_str) > max_chars:
        return result_str[:max_chars] + "\n... [TRUNCATED]"
    return result_str


def remove_none_values(value):
    if isinstance(value, dict):
        return {
            key: remove_none_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [remove_none_values(item) for item in value]
    return value


def prepare_tool_result_for_llm(tool_name: str, result_str: str) -> str:
    critical_tools = {
        "calculate_replenishment",
        "get_stock_replenishment_needed",
        "create_procurement_plan",
    }

    if tool_name in critical_tools:
        return result_str

    return truncate_tool_result(result_str, max_chars=5000)





def build_repair_instruction(user_query: str, plan: dict, execution: dict, goal: str) -> str:
    """Basarisiz bir plan icin LLM'e verilecek onarim talimatini uretir.

    Hem CLI dongusu hem de web_api ayni talimati kullanir; tek kaynak.
    """
    rules_list = [
        "- Preserve the user intent.",
        f"- Preserve the original plan goal: {goal}.",
        "- Use only argument names present in the tool schema.",
        "- Never repeat the same invalid arguments.",
        "- Every tool call must include ALL required arguments from its schema. If a required argument (such as 'items') must come from another tool, add that retrieval step first and reference it with $from. Never call a tool with empty arguments.",
        "- Every tool call must be a separate step with a unique step ID (e.g. step_1, step_2). Do not embed tool calls/functions inside $from. The $from reference must strictly start with a valid step ID of a preceding step (e.g., 'step_1.products.0.id').",
    ]
    if goal in ("DRAFT", "ORDER"):
        rules_list.extend([
            "- Do not rebuild an existing procurement plan during a draft/order repair.",
            "- Preserve the selected products, filters, seller ratings, and objective.",
            "- Repair only the invalid draft conversion.",
            "- Never replace DRAFT intent with PLAN.",
            "- If the source is products, do not use replenishments_to_items.",
        ])

    repair_rules_str = "\n".join(rules_list)

    return (
        f"Original request:\n{user_query}\n\n"
        f"Previous execution plan:\n"
        f"{json.dumps(plan, ensure_ascii=False)}\n\n"
        f"Execution failed at:\n"
        f"{json.dumps(execution, ensure_ascii=False, default=str)}\n\n"
        "Create a corrected execution plan. "
        "Do not repeat the failed call with identical arguments.\n"
        "Rules for repair:\n"
        f"{repair_rules_str}\n"
        "- FILTER PLACEMENT RULES:\n"
        "  1. Product category filters belong to stock/replenishment tools (e.g., category parameter in calculate_replenishment, like calculate_replenishment(category='Elektronik')).\n"
        "  2. Seller rating filters belong only to marketplace procurement tools (e.g., filters.min_rating in create_procurement_plan).\n"
        "  3. Never pass category_name or category to create_procurement_plan.\n"
        "  4. Never pass min_rating or filters to list_low_stock.\n"
        "  5. Use only exact argument names defined in each tool schema.\n"
        "  6. Do not move an invalid argument to another tool unless that tool schema explicitly supports it.\n"
        "  7. For category filtering of low/critical/replenishment stock, use calculate_replenishment(category=\"Elektronik\") instead of list_low_stock."
    )


async def main():
    print("=" * 60)
    print("Smart Stock & Procurement AI Agent Host Started")
    print("=" * 60)

    # Initialize client with stock and marketplace servers
    client = MCPClient({
        "stock-server": STOCK_SERVER_PATH,
        "marketplace-server": MARKETPLACE_SERVER_PATH
    })

    # Initialize LLM Service
    llm = LLMService()

    try:
        # Start connection to MCP servers
        await client.connect()
        print("\nAll servers connected and tools indexed successfully.")
        print("Ready for user commands. Type 'exit' or 'quit' to close.\n")
        
        conversation_states = {"default": load_state()}
        last_successful_procurement_plan = conversation_states["default"].last_plan
        
        while True:
            try:
                user_query = input("Soru (Örn: Stokta azalan ürünleri bul ve en ucuz tekliften taslak sipariş oluştur): ").strip()
                if not user_query:
                    continue
                if user_query.lower() in ("exit", "quit"):
                    break
                
                # Get current conversation state
                conversation_id = "default"
                state = conversation_states.setdefault(conversation_id, ConversationState())
                state.last_user_message = user_query
                
                if user_query.lower() in ("clear", "reset"):
                    last_successful_procurement_plan = None
                    conversation_states[conversation_id] = ConversationState()
                    if os.path.exists(STATE_FILE):
                        try:
                            os.remove(STATE_FILE)
                        except Exception:
                            pass
                    print("Son başarılı plan ve sohbet hafızası temizlendi.")
                    continue
                

                
                # Determine permission level based on user query intent
                is_write_intent = any(w in user_query.lower() for w in ["sipariş", "taslak", "satın al", "oluştur", "yaz", "kaydet", "receive", "place", "draft", "order"])
                is_confirm_intent = state.pending_draft_id is not None and any(w in user_query.lower() for w in ["evet", "onay", "onayla", "onaylıyorum", "devam", "tamam", "yes", "confirm"])
                permission_level = "FULL" if (is_write_intent or is_confirm_intent) else "PLAN"
                print(f"[İzin Seviyesi]: {permission_level}")

                # Fetch available tool names
                tools_list = await client.list_tools()
                available_tool_names = {tool.name for tool in tools_list}

                valid_cached_plans = {}
                if is_plan_valid(state.last_cheapest_plan):
                    valid_cached_plans["last_cheapest_plan"] = state.last_cheapest_plan
                if is_plan_valid(state.last_fastest_plan):
                    valid_cached_plans["last_fastest_plan"] = state.last_fastest_plan

                print("\n[AI Plan Hazırlıyor...]")
                system_prompt = get_execution_plan_prompt(tools_list, last_successful_procurement_plan, state, valid_cached_plans)
                messages = [
                    {"role": "system", "content": system_prompt}
                ]
                messages.extend(state.history)
                messages.append({"role": "user", "content": user_query})

                # Step 1: Generate initial plan
                raw_plan = llm.generate(messages)
                
                print("\n[LLM PLÂN CEVABI]")
                print(repr(raw_plan))
                print("[LLM PLÂN CEVABI SONU]\n")

                try:
                    plan = parse_execution_plan(raw_plan)
                    goal = plan.get("goal", "").upper()
                except Exception as parse_err:
                    print(f"Plan ayrıştırma hatası: {parse_err}")
                    continue

                # Enforce write permissions at host level
                WRITE_TOOLS = {
                    "create_purchase_draft",
                    "place_order",
                    "create_incoming_order",
                    "create_incoming_orders",
                    "receive_order",
                    "receive_orders",
                }
                has_unauthorized_write = False
                for step in plan.get("steps", []):
                    if step["tool"] in WRITE_TOOLS and permission_level == "PLAN":
                        has_unauthorized_write = True
                        break
                
                if has_unauthorized_write:
                    print("[PLAN EXECUTOR] ENGELLENDİ: Kullanıcı yalnızca plan istedi. Taslak veya sipariş adımları çalıştırılamaz.")
                    continue

                # Step 2: Execute plan
                execution = await execute_plan(plan, client, available_tool_names, state)

                # Step 3: Check failure and repair if necessary
                if not execution["success"]:
                    print(f"\n[PLAN EXECUTOR HATA] Adım '{execution.get('failed_step')}' ({execution.get('failed_tool')}) başarısız oldu: {execution.get('error')}")
                    print("[AI Planı Onarıyor...]")
                    
                    repair_messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": build_repair_instruction(user_query, plan, execution, goal)}
                    ]

                    repaired_raw = llm.generate(repair_messages)
                    print("\n[LLM ONARILMIŞ PLÂN CEVABI]")
                    print(repr(repaired_raw))
                    print("[LLM ONARILMIŞ PLÂN CEVABI SONU]\n")

                    try:
                        repaired_plan = parse_execution_plan(repaired_raw)
                        has_unauthorized_write = False
                        for step in repaired_plan.get("steps", []):
                            if step["tool"] in WRITE_TOOLS and permission_level == "PLAN":
                                has_unauthorized_write = True
                                break
                        if has_unauthorized_write:
                            print("[PLAN EXECUTOR] ENGELLENDİ: Kullanıcı yalnızca plan istedi. Taslak veya sipariş adımları çalıştırılamaz.")
                            continue

                        execution = await execute_plan(repaired_plan, client, available_tool_names, state)
                        if execution["success"]:
                            plan = repaired_plan
                            goal = plan.get("goal", "").upper()
                    except Exception as rep_err:
                        print(f"Onarılmış plan hatası: {rep_err}")
                        continue

                # Step 4: Handle successful plan execution outcomes
                if execution["success"]:
                    final_result = execution["last_result"]
                    
                    # Update last successful procurement plan if create_procurement_plan was run and successful
                    for step in plan.get("steps", []):
                        if step["tool"] == "create_procurement_plan":
                            step_id = step.get("id") or f"step_{plan.get('steps', []).index(step) + 1}"
                            step_res = execution["results"].get(step_id)
                            if isinstance(step_res, dict) and step_res.get("success", False):
                                last_successful_procurement_plan = step_res
                                try:
                                    resolved_args = resolve_step_arguments(step.get("arguments", {}), execution["results"], state)
                                    objective = resolved_args.get("objective", "CHEAPEST")
                                    items = resolved_args.get("items", [])
                                    
                                    cached_plan = CachedProcurementPlan(
                                        objective=objective,
                                        items=items,
                                        result=step_res,
                                        saved_at=datetime.now(timezone.utc).isoformat()
                                    )
                                    if objective == "CHEAPEST":
                                        state.last_cheapest_plan = cached_plan
                                    elif objective == "FASTEST":
                                        state.last_fastest_plan = cached_plan
                                except Exception as ce:
                                    print(f"Error caching plan in state: {ce}")

                    print("\n" + "=" * 40)
                    print("NİHAİ CEVAP:")
                    print("=" * 40)

                    # Format final presentation
                    last_step_tool = None
                    if plan.get("steps"):
                        last_step_tool = plan["steps"][-1].get("tool")

                    final_response_text = ""
                    if goal == "REASON":
                        print("[AI Sonuçları Yorumluyor...]")
                        cleaned_results = clean_tool_results_for_reasoning(execution["results"])
                        reasoning_prompt = get_reasoning_prompt(user_query, cleaned_results)
                        reasoning_messages = [
                            {"role": "system", "content": reasoning_prompt}
                        ]
                        reasoned_answer = llm.generate(reasoning_messages)
                        final_response_text = reasoned_answer.strip()
                        print(final_response_text)
                    elif goal == "CHAT":
                        final_response_text = final_result.get("chat_answer", "")
                        print(final_response_text)
                    elif last_step_tool == "create_procurement_plan":
                        final_response_text = format_procurement_plan(final_result)
                        print(final_response_text)
                    elif last_step_tool == "create_purchase_draft":
                        final_response_text = format_purchase_draft(final_result)
                        print(final_response_text)
                    else:
                        final_response_text = format_final_answer(final_result)
                        print(final_response_text)

                    print("=" * 40 + "\n")

                    # Step 5: Interactive confirmation for draft / order goals
                    if goal == "DRAFT":
                        pending_draft = final_result
                        for step_res in execution["results"].values():
                            if isinstance(step_res, dict) and (step_res.get("draftId") or step_res.get("draft_id") or step_res.get("id")):
                                pending_draft = step_res

                        draft_id = (
                            pending_draft.get("draftId")
                            or pending_draft.get("draft_id")
                            or pending_draft.get("id")
                            if isinstance(pending_draft, dict) else None
                        )

                        if draft_id:
                            state.pending_draft_id = int(draft_id)
                    elif goal == "ORDER":
                        state.pending_draft_id = None

                    # Append successful execution to history
                    final_raw_response = repaired_raw if 'repaired_raw' in locals() else raw_plan
                    try:
                        # Strip markdown blocks if present
                        cleaned_raw = final_raw_response.strip()
                        if cleaned_raw.startswith("```json"):
                            cleaned_raw = cleaned_raw[7:]
                        elif cleaned_raw.startswith("```"):
                            cleaned_raw = cleaned_raw[3:]
                        if cleaned_raw.endswith("```"):
                            cleaned_raw = cleaned_raw[:-3]
                        cleaned_raw = cleaned_raw.strip()
                        
                        history_plan = json.loads(cleaned_raw)
                        
                        # Keep only a short summary to prevent prompt bloat
                        if len(final_response_text) > 200:
                            history_plan["final_response"] = final_response_text[:200] + "..."
                        else:
                            history_plan["final_response"] = final_response_text
                            
                        history_response_str = json.dumps(history_plan, ensure_ascii=False)
                    except Exception:
                        history_response_str = final_raw_response

                    state.history.append({"role": "user", "content": user_query})
                    state.history.append({"role": "assistant", "content": history_response_str})
                    if len(state.history) > 2:
                        state.history = state.history[-2:]
                else:
                    print(f"İşlem başarısız oldu: {execution.get('error')}")

                # Persist conversation state to disk
                state.last_plan = last_successful_procurement_plan
                save_state(state)

            except KeyboardInterrupt:
                print("\nİşlem kullanıcı tarafından iptal edildi.")
                continue
            except Exception as loop_err:
                print(f"\nDöngü sırasında hata: {loop_err}")
                continue
    finally:
        # Cleanly shut down connections
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKapatılıyor...")

