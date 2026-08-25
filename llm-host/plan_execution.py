"""Execution-plan argument resolution and tool execution.

This module owns the deterministic mechanics between a validated execution
plan and MCP tool calls: transforms, context/step references, result
normalization, empty-input business guards, and execution bookkeeping.
It intentionally has no FastAPI, Ollama, or concrete MCP-server dependency.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from plan_validation import ALLOWED_CONTEXT_SOURCES


def replenishments_to_items(value):
    items = []
    for replenishment in value or []:
        product_id = replenishment.get("productId") or replenishment.get("product_id")
        quantity = replenishment.get("replenishmentQuantityNeeded") or replenishment.get("quantity")
        if product_id is None or quantity is None or quantity <= 0:
            continue
        items.append({"product_id": int(product_id), "quantity": int(quantity)})
    return items


def _stock_products_to_items(products):
    if isinstance(products, dict):
        products = products.get("products") or []
    items = []
    for product in products or []:
        product_id = product.get("id") or product.get("productId")
        stock = product.get("stockQuantity", 0)
        target = product.get("targetStock")
        if product_id is None or target is None:
            continue
        quantity = int(target) - int(stock)
        if quantity > 0:
            items.append({"product_id": int(product_id), "quantity": quantity})
    return items


def out_of_stock_products_to_items(products):
    return _stock_products_to_items(products)


def low_stock_products_to_items(products):
    return _stock_products_to_items(products)


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

    def product_id_from(*candidates):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            product_id = candidate.get("product_id") or candidate.get("productId")
            if product_id is None and isinstance(candidate.get("product"), dict):
                product_id = candidate["product"].get("id")
            if product_id is not None:
                return int(product_id)
        return None

    def append_item(offer_id, quantity, product_id):
        # A draft offer without its expected inventory product cannot be checked
        # against the marketplace database.  Dropping it makes the executor's
        # empty-input guard stop the write instead of guessing an association.
        if offer_id is None or quantity is None or quantity <= 0 or product_id is None:
            return
        draft_items.append({
            "product_id": int(product_id),
            "offer_id": int(offer_id),
            "quantity": int(quantity),
        })

    draft_items = []
    if isinstance(value, list):
        for item in value or []:
            allocations = item.get("allocations")
            if allocations:
                for allocation in allocations:
                    offer_id = allocation.get("offer_id") or allocation.get("id")
                    quantity = allocation.get("quantity")
                    append_item(offer_id, quantity, product_id_from(allocation, item))
            else:
                # A generic `id` on a procurement item may be a product ID.  Only
                # an explicit offer_id is safe at this level.
                offer_id = item.get("offer_id") or item.get("offerId")
                quantity = item.get("quantity")
                append_item(offer_id, quantity, product_id_from(item))
    elif isinstance(value, dict):
        allocations = value.get("allocations")
        if allocations:
            for allocation in allocations:
                offer_id = allocation.get("offer_id") or allocation.get("id")
                quantity = allocation.get("quantity")
                append_item(offer_id, quantity, product_id_from(allocation, value))
        else:
            selected_offer = value.get("selected_offer")
            if selected_offer:
                offer_id = selected_offer.get("offer_id") or selected_offer.get("id")
                quantity = (
                    value.get("requested_quantity")
                    or value.get("quantity")
                    or selected_offer.get("quantity")
                    or 1
                )
                append_item(offer_id, quantity, product_id_from(selected_offer, value))
            else:
                offer_id = value.get("offer_id") or value.get("offerId")
                quantity = value.get("quantity") or 1
                append_item(offer_id, quantity, product_id_from(value))
    return draft_items


def order_to_incoming_items(value):
    """Convert a place_order result into create_incoming_orders items."""
    if not isinstance(value, dict):
        raise ValueError("order_to_incoming_items yalnizca place_order sonucu kabul eder.")
    items = value.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Siparis icinde kalem bulunamadi.")

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

TRANSFORM_INPUT_TYPES = {
    "out_of_stock_products_to_items": "product_list",
    "low_stock_products_to_items": "product_list",
    "replenishments_to_items": "replenishment_list",
    "plan_to_draft_items": {"procurement_plan", "comparison_response"},
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


def serialize_plan(plan):
    if plan is None or isinstance(plan, dict):
        return plan
    if hasattr(plan, "__dataclass_fields__"):
        return {name: getattr(plan, name) for name in plan.__dataclass_fields__}
    return plan


def get_product_id(data: dict) -> int | None:
    if not isinstance(data, dict):
        return None
    value = data.get("productId") or data.get("product_id") or data.get("id")
    return int(value) if value is not None else None


def get_product_name(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    return data.get("productName") or data.get("product_name") or data.get("name")


def get_replenishment_quantity(data: dict) -> int | None:
    if not isinstance(data, dict):
        return None
    value = (
        data.get("replenishmentQuantityNeeded")
        or data.get("quantity")
        or data.get("replenishment_quantity_needed")
    )
    return int(value) if value is not None else None


def update_last_product(state, product_id: int, product_name: str):
    if state.last_product and state.last_product.get("id") == product_id:
        state.last_product["name"] = product_name
    else:
        state.last_product = {"id": product_id, "name": product_name}
        state.last_replenishment = None


def item_matches_query(item_name: str, query: str) -> bool:
    if not item_name or not query:
        return False
    query_words = [word.strip(".,!?\"'()").lower() for word in query.split()]
    query_words = [word for word in query_words if len(word) >= 3 and not word.isdigit()]
    item_name_lower = item_name.lower()
    for query_word in query_words:
        if query_word in item_name_lower:
            return True
        for item_word in [word.lower() for word in item_name.split() if len(word) >= 3]:
            if item_word in query_word:
                return True
    return False


def filter_list_by_query(items: list, query: str) -> list:
    if not query or not items:
        return items
    matching = []
    for item in items:
        name = None
        if isinstance(item, dict):
            name = item.get("productName") or item.get("product_name") or item.get("name") or item.get("title")
        else:
            for attr in ("productName", "product_name", "name"):
                if hasattr(item, attr):
                    name = getattr(item, attr)
                    break
        if name and item_matches_query(name, query):
            matching.append(item)
    return matching if matching and len(matching) < len(items) else items


def resolve_context_path(state, path: str):
    parts = path.split(".")
    first = parts[0]
    if first == "last_reference":
        if not state.last_reference_id:
            return None
        reference = state.references.get(state.last_reference_id)
        if not reference:
            return None
        current = reference.get("data")
    else:
        current = getattr(state, first, None)

    if getattr(state, "last_user_message", None):
        if isinstance(current, list):
            current = filter_list_by_query(current, state.last_user_message)
        elif isinstance(current, dict):
            current = dict(current)
            for key in ("items", "products", "replenishments", "offers"):
                if key in current and isinstance(current[key], list):
                    current[key] = filter_list_by_query(current[key], state.last_user_message)

    for part in parts[1:]:
        if current is None:
            return None
        if isinstance(current, dict):
            value = current.get(part)
            if value is None:
                nested = next(
                    (current[key] for key in ("items", "products", "replenishments", "offers")
                     if isinstance(current.get(key), list)),
                    None,
                )
                if nested is None:
                    current = None
                    continue
                projected = []
                for item in nested:
                    if isinstance(item, dict):
                        item_value = item.get(part)
                        if item_value is None and part in {"id", "productId", "product_id"}:
                            item_value = item.get("productId") or item.get("product_id") or item.get("id")
                        if item_value is not None:
                            projected.append(item_value)
                    elif hasattr(item, part):
                        item_value = getattr(item, part)
                        if item_value is not None:
                            projected.append(item_value)
                current = projected
            else:
                current = value
        elif isinstance(current, list):
            if part.isdigit():
                index = int(part)
                if not 0 <= index < len(current):
                    return None
                current = current[index]
            else:
                projected = []
                for item in current:
                    if isinstance(item, dict):
                        value = item.get(part)
                        if value is None and part in {"id", "productId", "product_id"}:
                            value = item.get("productId") or item.get("product_id") or item.get("id")
                        if value is not None:
                            projected.append(value)
                    elif hasattr(item, part):
                        value = getattr(item, part)
                        if value is not None:
                            projected.append(value)
                current = projected
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

    if isinstance(current, list) and len(current) == 1 and not isinstance(current[0], (dict, list)):
        return current[0]
    return current


def save_reference(state, reference_type: str, source_tool: str, data: Any) -> str:
    reference_id = f"ref_{uuid4().hex[:8]}"
    state.references[reference_id] = {
        "type": reference_type,
        "source_tool": source_tool,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": len(data) if isinstance(data, list) else 1,
        "data": data,
    }
    state.last_reference_id = reference_id
    return reference_id


def resolve_context_reference(value: dict, state):
    source_context = value.get("$from_context")
    source_root = source_context.split(".")[0]
    if source_root not in ALLOWED_CONTEXT_SOURCES:
        raise ValueError(f"Desteklenmeyen context referansı: {source_context}")

    if source_context == "last_reference":
        reference_id = state.last_reference_id
        if not reference_id:
            raise ValueError("Bu işlem için kullanılabilecek önceki bir sonuç bulunamadı.")
        reference = state.references.get(reference_id)
        if not reference:
            raise ValueError(f"Context referansı bulunamadı: {reference_id}")
        resolved = reference["data"]
        transform_name = value.get("$transform")
        if transform_name:
            transform = TRANSFORMS.get(transform_name)
            if transform is None:
                raise ValueError(f"Bilinmeyen transform: {transform_name}")
            expected = TRANSFORM_INPUT_TYPES.get(transform_name)
            if expected:
                allowed = expected if isinstance(expected, set) else {expected}
                if reference["type"] not in allowed:
                    raise ValueError(f"{transform_name}, {reference['type']} tipinde veri kabul etmiyor.")
            resolved = transform(resolved)
        return resolved

    resolved = resolve_context_path(state, source_context)
    if resolved is None:
        raise ValueError(f"Context referansı bulunamadı veya boş: {source_context}")
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
        if step_id in ALLOWED_CONTEXT_SOURCES and step_id not in execution_results:
            if state is None:
                raise ValueError("Conversation state context resolution icin gerekli.")
            coerced = dict(value)
            coerced["$from_context"] = coerced.pop("$from")
            return resolve_context_reference(coerced, state)

        step_result = execution_results.get(step_id)
        if step_result is None:
            raise ValueError(f"Kaynak adım sonucu bulunamadı: {step_id}")
        resolved = step_result if not result_path else get_nested_value(step_result, result_path)
        if (
            not resolved
            and result_path == "items"
            and isinstance(step_result, dict)
            and "selected_offer" in step_result
        ):
            resolved = step_result
        if resolved is None and result_path:
            keys = list(step_result.keys()) if isinstance(step_result, dict) else step_result
            raise ValueError(f"Referans çözülemedi: {source} (step sonucu: {keys})")
        transform_name = value.get("$transform")
        if transform_name:
            transform = TRANSFORMS.get(transform_name)
            if transform is None:
                raise ValueError(f"Bilinmeyen transform: {transform_name}")
            resolved = transform(resolved)
        return resolved

    return {key: resolve_argument_value(item, execution_results, state) for key, item in value.items()}


def resolve_step_arguments(arguments, execution_results, state=None):
    return {key: resolve_argument_value(value, execution_results, state) for key, value in arguments.items()}


def extract_result_text(result) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = [getattr(item, "text", None) for item in content]
        texts = [text for text in texts if text]
        if texts:
            return "\n".join(texts)
    return str(result)


def normalize_tool_result(result) -> dict:
    # Unit tests and in-process adapters may already return decoded mappings,
    # while MCP transport returns content blocks containing JSON text.
    if isinstance(result, dict):
        if result.get("success") is False:
            return result
        return {"success": True, "data": result}
    if getattr(result, "isError", False):
        return {"success": False, "error": extract_result_text(result)}
    text = extract_result_text(result)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("success") is False:
            return parsed
        return {"success": True, "data": parsed}
    except json.JSONDecodeError:
        markers = ("error", "hata", "invalid", "failed", "required", "no items provided")
        if any(marker in text.lower() for marker in markers):
            return {"success": False, "error": text}
        return {"success": True, "data": {"raw_result": text}}


def remove_none_values(value):
    if isinstance(value, dict):
        return {key: remove_none_values(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [remove_none_values(item) for item in value]
    return value


COLLECTION_ARGUMENTS = {
    "create_procurement_plan": "items",
    "create_purchase_draft": "items",
    "create_incoming_orders": "items",
    "receive_orders": "order_ids",
}
EMPTY_SOURCE_REASONS = {
    "list_low_stock": "Kritik seviyede ürün bulunmuyor",
    "list_out_of_stock": "Stokta tükenmiş ürün bulunmuyor",
    "list_products": "Ürün listesi boş",
    "search_products": "Aramayla eşleşen ürün bulunamadı",
    "calculate_replenishment": "Şu an sipariş edilmesi gereken ürün bulunmuyor",
    "get_stock_replenishment_needed": "Şu an sipariş edilmesi gereken ürün bulunmuyor",
    "list_incoming_orders": "Teslim alınmayı bekleyen sipariş bulunmuyor",
    "create_procurement_plan": "Satın alma planı boş kaldı",
}
TARGET_ACTIONS = {
    "create_procurement_plan": "satın alma planı oluşturulamadı",
    "create_purchase_draft": "sipariş taslağı oluşturulamadı",
    "create_incoming_orders": "beklenen stok kaydı yapılamadı",
    "receive_orders": "teslim alma işlemi yapılamadı",
}


def detect_empty_input(tool_name: str, arguments: dict, step: dict, plan: dict):
    key = COLLECTION_ARGUMENTS.get(tool_name)
    if not key:
        return None
    value = arguments.get(key)
    if not isinstance(value, list) or value:
        return None

    raw = (step.get("arguments") or {}).get(key)
    source_tool = None
    if isinstance(raw, dict) and isinstance(raw.get("$from"), str):
        source_step_id = raw["$from"].partition(".")[0]
        for index, other in enumerate(plan.get("steps") or []):
            if isinstance(other, dict) and (other.get("id") or f"step_{index + 1}") == source_step_id:
                source_tool = other.get("tool")
                break
    reason = EMPTY_SOURCE_REASONS.get(source_tool, "Bu işlem için uygun kalem bulunamadı")
    action = TARGET_ACTIONS.get(tool_name, "işlem tamamlanamadı")
    return f"{reason}, bu yüzden {action}."


async def execute_plan(plan: dict, client, available_tool_names: set[str], state=None):
    if plan.get("goal", "").upper() == "CHAT":
        return {
            "success": True,
            "results": {},
            "last_result": {"success": True, "chat_answer": plan.get("answer", "")},
        }

    execution_results = {}
    step_durations = {}
    declared_sources = plan.get("context_sources") or []
    for source in declared_sources:
        value = getattr(state, source, None) if state is not None else None
        if value is not None:
            execution_results[source] = serialize_plan(value)
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

        step_started = time.perf_counter()
        try:
            arguments = resolve_step_arguments(step.get("arguments", {}), execution_results, state)
            arguments = remove_none_values(arguments)
            empty_reason = detect_empty_input(tool_name, arguments, step, plan)
            if empty_reason:
                return {
                    "success": False,
                    "failed_step": step_id,
                    "failed_tool": tool_name,
                    "error": empty_reason,
                    "business_reason": empty_reason,
                    "retryable": False,
                    "results": execution_results,
                }

            if tool_name == "calculate_replenishment":
                category = arguments.get("category")
                invalid = {"kritik", "critical", "azalan", "low stock", "stokta olmayan", "out of stock"}
                if isinstance(category, str) and category.lower().strip() in invalid:
                    arguments.pop("category", None)
            if tool_name in {"create_procurement_plan", "compare_offers", "search_offers"}:
                arguments.pop("category", None)
                arguments.pop("category_name", None)
            if tool_name == "list_incoming_orders" and plan.get("goal", "").upper() == "RECEIVE":
                arguments["pending_only"] = True
                arguments["ready_only"] = True

            expected = arguments.get("expected_delivery_date") or arguments.get("expectedDeliveryDate")
            if expected:
                try:
                    expected_date = date.fromisoformat(str(expected).split("T")[0])
                    if expected_date < date.today():
                        raise ValueError("Teslimat tarihi geçmiş bir tarih olamaz.")
                except ValueError as exc:
                    raise ValueError(f"Geçersiz veya geçmiş teslimat tarihi: {expected}.") from exc

            print(f"[PLAN EXECUTOR] {step_id}: {tool_name}({arguments})")
            raw_result = await client.call_tool(tool_name, arguments)
            step_durations[step_id] = round((time.perf_counter() - step_started) * 1000)
            normalized = normalize_tool_result(raw_result)
            if not normalized.get("success", True):
                return {
                    "success": False,
                    "failed_step": step_id,
                    "failed_tool": tool_name,
                    "error": normalized.get("error") or normalized.get("message") or "Tool başarısız oldu.",
                    "results": execution_results,
                    "durations_ms": step_durations,
                }

            result_data = normalized.get("data") if isinstance(normalized.get("data"), dict) else normalized
            execution_results[step_id] = result_data
            if state is not None:
                if tool_name in {"list_out_of_stock", "list_low_stock", "search_products", "list_products"}:
                    products = result_data.get("products") if isinstance(result_data, dict) else []
                    save_reference(state, "product_list", tool_name, products)
                    if products and isinstance(products, list):
                        product_id = get_product_id(products[0])
                        product_name = get_product_name(products[0])
                        if product_id is not None and product_name is not None:
                            update_last_product(state, product_id, product_name)
                elif tool_name in {"calculate_replenishment", "get_stock_replenishment_needed"}:
                    replenishments = result_data.get("replenishments") if isinstance(result_data, dict) else []
                    save_reference(state, "replenishment_list", tool_name, replenishments)
                    if replenishments and isinstance(replenishments, list):
                        first = replenishments[0]
                        product_id = get_product_id(first)
                        product_name = get_product_name(first)
                        quantity = get_replenishment_quantity(first)
                        if product_id is not None:
                            if product_name:
                                update_last_product(state, product_id, product_name)
                            else:
                                current_name = state.last_product.get("name") if state.last_product else "Bilinmeyen Ürün"
                                update_last_product(state, product_id, current_name)
                            state.last_replenishment = {
                                "product_id": product_id,
                                "replenishment_quantity_needed": quantity if quantity is not None else 0,
                            }
                elif tool_name == "create_procurement_plan":
                    save_reference(state, "procurement_plan", tool_name, result_data)
                elif tool_name in {"compare_offers", "search_offers"}:
                    save_reference(state, "comparison_response", tool_name, result_data)
        except Exception as exc:
            step_durations[step_id] = round((time.perf_counter() - step_started) * 1000)
            return {
                "success": False,
                "failed_step": step_id,
                "failed_tool": tool_name,
                "error": str(exc),
                "results": execution_results,
                "durations_ms": step_durations,
            }

    steps = plan.get("steps", [])
    if not steps:
        return {
            "success": True,
            "results": execution_results,
            "last_result": execution_results,
            "durations_ms": step_durations,
        }
    return {
        "success": True,
        "results": execution_results,
        "last_result": execution_results.get(steps[-1].get("id")) or execution_results.get(f"step_{len(steps)}"),
        "durations_ms": step_durations,
    }
