"""Pure execution-plan parsing and state validation.

This module contains no MCP, database, or network dependencies.  It is the
host-side safety boundary for turning model output into an executable plan.
"""
import json
import re
from copy import deepcopy


ALLOWED_CONTEXT_SOURCES = {
    "last_plan",
    "last_cheapest_plan",
    "last_fastest_plan",
    "last_product",
    "last_replenishment",
    "last_reference",
    "pending_draft_id",
    "pending_receive_ids",
}

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


def normalize_redundant_plan_comparison(plan: dict) -> dict:
    """Drop a redundant trailing ``compare_offers`` from a two-plan comparison.

    ``compare_offers`` is a single-product tool.  For a cheapest-vs-fastest
    procurement request the two multi-product procurement results are already
    the complete inputs to the host-side comparison.  Small local models can
    nevertheless append ``compare_offers`` with array arguments.  Removing
    only that exact, trailing shape is lossless; all other offer comparisons
    remain untouched.
    """
    if not isinstance(plan, dict) or str(plan.get("goal", "")).upper() != "REASON":
        return plan

    steps = plan.get("steps")
    if not isinstance(steps, list) or len(steps) < 3:
        return plan
    if not isinstance(steps[-1], dict) or steps[-1].get("tool") != "compare_offers":
        return plan

    procurement_steps = [
        step for step in steps[:-1]
        if isinstance(step, dict) and step.get("tool") == "create_procurement_plan"
    ]
    if len(procurement_steps) != 2:
        return plan

    objectives = {
        str((step.get("arguments") or {}).get("objective", "")).upper()
        for step in procurement_steps
    }
    if objectives != {"CHEAPEST", "FASTEST"}:
        return plan

    item_sources = [(step.get("arguments") or {}).get("items") for step in procurement_steps]
    if item_sources[0] is None or item_sources[0] != item_sources[1]:
        return plan

    normalized = deepcopy(plan)
    normalized["steps"] = normalized["steps"][:-1]
    return normalized


def validate_draft_offer_source(steps: list[dict]) -> None:
    """Require draft offers to come from an authoritative plan result.

    Literal offer IDs emitted by the model are not trustworthy: an ID can point
    at a completely different product in the current database.  The draft input
    must therefore be derived from a marketplace procurement plan (or a
    server-owned prior plan/reference) through plan_to_draft_items.
    """
    draft_step = steps[-1]
    items = (draft_step.get("arguments") or {}).get("items")
    if not isinstance(items, dict) or items.get("$transform") != "plan_to_draft_items":
        raise ValueError(
            "DRAFT planında teklif kimlikleri doğrudan yazılamaz; "
            "create_purchase_draft kalemleri doğrulanmış satın alma planından üretilmelidir."
        )

    source = items.get("$from")
    context_source = items.get("$from_context")
    if source:
        source_step_id = str(source).partition(".")[0]
        source_step = next(
            (step for step in steps[:-1] if str(step.get("id")) == source_step_id),
            None,
        )
        if not source_step or source_step.get("tool") not in {
            "create_procurement_plan",
            "compare_offers",
        }:
            raise ValueError(
                "DRAFT teklifleri aynı plandaki doğrulanmış marketplace seçiminden gelmelidir."
            )
        return

    if context_source:
        root = str(context_source).partition(".")[0]
        safe_contexts = {
            "last_plan",
            "last_cheapest_plan",
            "last_fastest_plan",
            "last_reference",
        }
        if root not in safe_contexts:
            raise ValueError("DRAFT için güvenilir bir önceki satın alma planı bulunamadı.")
        return

    raise ValueError("DRAFT tekliflerinin doğrulanmış kaynak adımı eksik.")


def remove_json_comments(text: str) -> str:
    """Strip JavaScript-style comments without touching quoted strings."""
    comment_pattern = r'("(?:\\.|[^"\\])*")|(?:\/\*(?:[^*]|\*(?!\/))*\*\/)|(?:\/\/.*)'

    def replace(match):
        return match.group(1) if match.group(1) else ""

    return re.sub(comment_pattern, replace, text)


def parse_execution_plan(text: str) -> dict:
    """Parse and structurally validate one model-generated execution plan."""
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
                            break
                        except Exception:
                            pass
            else:
                raise parse_err

    if parsed.get("type") != "execution_plan":
        raise ValueError("Beklenen response type: execution_plan")

    goal = parsed.get("goal", "").upper()

    if goal == "CHAT":
        if not parsed.get("answer"):
            raise ValueError("CHAT planı için 'answer' alanı bulunmalıdır.")
        if parsed.get("steps"):
            raise ValueError("CHAT planı adımlar (steps) içeremez.")
        return parsed

    parsed.setdefault("steps", [])
    steps = parsed["steps"]

    if not steps:
        if not (goal == "REASON" and parsed.get("context_sources")):
            raise ValueError("Execution plan içinde steps bulunmalıdır.")

    context_sources = parsed.get("context_sources")
    if context_sources is not None:
        if not isinstance(context_sources, list):
            raise ValueError("context_sources list tipinde olmalıdır.")
        for source in context_sources:
            if source not in ALLOWED_CONTEXT_SOURCES:
                raise ValueError(f"Geçersiz context source: {source}")

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] object olmalıdır.")
        if not step.get("tool"):
            raise ValueError(f"steps[{index}] içinde tool eksik.")
        if "params" in step:
            raise ValueError(f"steps[{index}] 'params' içeremez, 'arguments' kullanılmalıdır.")
        if "arguments" in step and not isinstance(step.get("arguments"), dict):
            raise ValueError(f"steps[{index}].arguments object olmalıdır.")

    if goal == "INFO":
        allowed_info_tools = INFO_TOOLS | {"calculate_replenishment", "get_stock_replenishment_needed"}
        for step in steps:
            if step["tool"] not in allowed_info_tools:
                raise ValueError(
                    f"INFO planı sadece salt okunur araçlar içerebilir. Geçersiz araç: {step['tool']}"
                )
    elif goal == "PLAN":
        if not steps or steps[-1]["tool"] != "create_procurement_plan":
            raise ValueError("PLAN planının son adımı 'create_procurement_plan' olmalıdır.")
    elif goal == "DRAFT":
        if not steps or steps[-1]["tool"] != "create_purchase_draft":
            raise ValueError("DRAFT planının son adımı 'create_purchase_draft' olmalıdır.")
        for step in steps:
            if step["tool"] == "place_order":
                raise ValueError("DRAFT planı 'place_order' adımı içeremez.")
        validate_draft_offer_source(steps)
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
        if not steps:
            raise ValueError("RECEIVE planında en az bir adım bulunmalıdır.")
        allowed = {
            "list_incoming_orders",
            "list_marketplace_orders",
            "get_order_status",
            "receive_order",
            "receive_orders",
        }
        for step in steps:
            if step["tool"] not in allowed:
                raise ValueError(
                    "RECEIVE planı yalnızca teslim alma araçlarını içerebilir. "
                    f"Geçersiz araç: {step['tool']}"
                )
    elif goal == "REASON":
        for step in steps:
            if step["tool"] in WRITE_TOOLS:
                raise ValueError("REASON planı yazma araçları (write tools) içeremez.")

    return parsed


def validate_plan_against_state(plan: dict, state) -> None:
    """Reject plans that are syntactically valid but unsafe for current state."""
    goal = (plan.get("goal") or "").upper()
    steps = plan.get("steps") or []

    wants_order = goal == "ORDER" or any(
        step.get("tool") == "place_order" for step in steps if isinstance(step, dict)
    )
    wants_receive = any(
        step.get("tool") in ("receive_order", "receive_orders")
        for step in steps
        if isinstance(step, dict)
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
