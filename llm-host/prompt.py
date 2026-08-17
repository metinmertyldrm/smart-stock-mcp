import json

SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "additionalProperties",
}


def compact_schema(value):
    if isinstance(value, list):
        return [compact_schema(item) for item in value]

    if not isinstance(value, dict):
        return value

    result = {}

    for key, item in value.items():
        if key not in SCHEMA_KEYS:
            continue

        if key == "properties" and isinstance(item, dict):
            result[key] = {
                name: compact_schema(prop)
                for name, prop in item.items()
            }
        else:
            result[key] = compact_schema(item)

def compact_plan_for_prompt(plan):
    if not plan:
        return None
    
    if not isinstance(plan, dict):
        if hasattr(plan, "result") and hasattr(plan, "objective"):
            plan_dict = {
                "objective": getattr(plan, "objective", None),
                "items": getattr(plan, "items", None),
                "result": getattr(plan, "result", None)
            }
        else:
            try:
                from dataclasses import asdict
                plan_dict = asdict(plan)
            except Exception:
                plan_dict = str(plan)
    else:
        plan_dict = plan

    if not isinstance(plan_dict, dict):
        return plan_dict

    compacted = {}
    if "objective" in plan_dict:
        compacted["objective"] = plan_dict["objective"]
    
    result = plan_dict.get("result")
    if result is None:
        result = plan_dict

    if isinstance(result, dict):
        compacted_res = {}
        for key in ["success", "complete", "draft_id", "draftId", "id"]:
            if key in result:
                compacted_res[key] = result[key]
        
        items = result.get("items")
        if isinstance(items, list):
            compacted_items = []
            for item in items:
                comp_item = {}
                p_id = item.get("product_id") or item.get("productId") or item.get("id")
                qty = item.get("quantity") or item.get("requested_quantity") or item.get("required_quantity")
                if p_id is not None:
                    comp_item["product_id"] = p_id
                if qty is not None:
                    comp_item["quantity"] = qty
                if comp_item:
                    compacted_items.append(comp_item)
            compacted_res["items"] = compacted_items
        compacted["result"] = compacted_res
    else:
        items = plan_dict.get("items")
        if isinstance(items, list):
            compacted["items"] = [{"product_id": i.get("product_id"), "quantity": i.get("quantity")} for i in items if isinstance(i, dict)]

    return compacted


def get_reasoning_prompt(user_query: str, tool_results: dict) -> str:
    """
    Generates a prompt for the Reasoning Engine.
    The engine takes the user's query and the structured tool results/context sources,
    then interprets them in natural, helpful Turkish.
    """
    results_str = json.dumps(tool_results, ensure_ascii=False, indent=2)
    return f"""Smart Stock & Procurement AI Mantıksal Akıl Yürütme (Reasoning) Katmanısın.
Görevin, kullanıcının sorusunu ve bu soruya cevap bulmak için arka planda çalıştırılan araçların (tool) çıktılarını veya bağlam verilerini (context_sources) analiz ederek, Türkçe dilinde doğal, mantıklı ve yardımcı bir cevap/öneri üretmektir.

Kullanıcı Sorusu:
{user_query}

Araç (Tool) Çıktıları / Bağlam Verileri:
{results_str}

Lütfen bu verileri analiz et ve kullanıcının sorusuna doğrudan, açıklayıcı ve mantıklı bir cevap yaz.
Kurallar:
1. Kesinlikle uydurma veri, fiyat veya stok miktarı yazma. Yalnızca yukarıdaki verileri kullan.
2. Karşılaştırma yaparken fiyat farklarını, teslimat sürelerini ve satıcı puanlarını açıkça belirt ve hangisinin neden daha mantıklı olduğunu açıklayın.
3. KARAR VE SEÇENEK MANTIĞI: Eğer kullanıcı iki veya daha fazla seçenek arasında seçim yapmak istiyorsa (örn: "3 tane mi 4 tane mi almalıyım?", "A satıcısı mı mantıklı B mi?", "X ürünü yerine Y mi alalım?"):
   - Bu seçenekleri araç verileriyle (gerçek ihtiyaç miktarları, birim fiyatlar, kargo ücretleri, teslimat günleri vb.) karşılaştır.
   - Matematiksel ve rasyonel olarak gerçek ihtiyaca veya en iyi fayda/maliyet dengesine en yakın/uygun seçeneği belirle.
   - Neden o seçeneğin daha mantıklı olduğunu (örn: "4 adet almanız, gereken 5 adete daha yakın olduğu için daha mantıklıdır" veya "A satıcısının fiyatı %5 pahalı olsa da 3 gün daha hızlı olduğu için daha avantajlıdır" gibi) matematiksel veya mantıksal gerekçeleriyle açıkla.
4. Cevap sade, net ve Türkçe olmalıdır. JSON formatında DEĞİL, doğrudan konuşma dilinde yaz. Uzun listeler veya gereksiz teknik detaylar yerine doğrudan kullanıcının sorusunun cevabını ver.
5. Eğer kullanıcı ürünlerin sipariş miktarını soruyorsa (örn: "kaç tane sipariş vermek gerek"), her ürün için gereken sipariş miktarını (replenishmentQuantityNeeded veya quantity) doğrudan, net bir cümleyle belirt (Örn: "Galaxy S24 için 8 adet, A4 Fotokopi Kağıdı için 30 adet sipariş verilmesi gerekmektedir."). "Veri yok" veya "bilgi alamadık" gibi ifadeler ASLA kullanma.
6. Eğer bağlamda hem 'last_cheapest_plan' (en ucuz satın alma planı) hem de 'last_fastest_plan' (en hızlı satın alma planı) bulunuyorsa ve kullanıcı bunlar arasında karşılaştırma, farkları veya hangisinin daha mantıklı olduğunu soruyorsa, bu iki planın toplam fiyatlarını, teslimat sürelerini ve satıcıların genel durumunu karşılaştırarak hangisinin hangi açıdan avantajlı olduğunu detaylıca açıkla.
"""


def get_execution_plan_prompt(tools_list: list, last_successful_plan: dict | None = None, state=None, valid_cached_plans: dict | None = None) -> str:
    """
    Generates a prompt for execution plan generation with dynamic tools and rules.
    """
    tools_str = ""
    for tool in tools_list:
        schema = compact_schema(tool.inputSchema)
        if isinstance(schema, dict):
            schema.pop("additionalProperties", None)
            if schema.get("type") == "object":
                schema = schema.get("properties", {})
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
        tools_str += f"- {tool.name}: {tool.description} | Arguments: {schema_json}\n"

    plan_info = ""
    if last_successful_plan:
        compacted_plan = compact_plan_for_prompt(last_successful_plan)
        plan_info = f"\nLAST_PLAN: {json.dumps(compacted_plan, ensure_ascii=False)}\n"

    cached_info = ""
    if valid_cached_plans:
        for name, plan in valid_cached_plans.items():
            if plan:
                compacted_plan = compact_plan_for_prompt(plan)
                cached_info += f"\n{name.upper()}: {json.dumps(compacted_plan, ensure_ascii=False)}\n"

    context_info = ""
    if state:
        if state.last_reference_id:
            ref_id = state.last_reference_id
            ref_data = state.references.get(ref_id)
            if ref_data:
                context_info += f"\nLAST_REFERENCE: id={ref_id}, type={ref_data['type']}, source={ref_data['source_tool']}, count={ref_data['count']}\n"
        if state.pending_draft_id:
            context_info += f"\nPENDING_DRAFT_ID: {state.pending_draft_id}\n"

    return f"""Smart Stock & Procurement AI Executor.

AVAILABLE TOOLS:
{tools_str.strip()}
{plan_info}{cached_info}{context_info}
TASK: Analyze user request, output single JSON of type "execution_plan".

GOALS:
- CHAT: Conversation/formatting/simplification of history (no tools). Answer directly in "answer" field.
- REASON: Advice/decision requiring tool data.
- INFO: Retrieve and display data.
- PLAN: Build procurement plan (ends with create_procurement_plan).
- DRAFT: Create purchase draft (ends with create_purchase_draft).
- ORDER: Finalize draft (ends with place_order).

EXAMPLES:
1. "iPhone 4 mü 3 mü almalıyım?" -> goal: REASON, steps:
   - step_1: search_products(arguments: query="iPhone")
   - step_2: calculate_replenishment(arguments: product_id={{"$from": "step_1.products.0.id"}})
2. "En hızlı teklif pahalı mı?" -> goal: REASON, steps:
   - step_1: calculate_replenishment
   - step_2: create_procurement_plan(arguments: items={{"$from": "step_1.replenishments", "$transform": "replenishments_to_items"}}, objective="CHEAPEST")
3. "Kritik ürünler için satın alma planı hazırla" -> goal: PLAN, steps:
   - step_1: calculate_replenishment
   - step_2: create_procurement_plan(arguments: items={{"$from": "step_1.replenishments", "$transform": "replenishments_to_items"}}, objective="CHEAPEST")
4. "iPhone için taslak sipariş oluştur" -> goal: DRAFT, steps:
   - step_1: search_products(arguments: query="iPhone")
   - step_2: compare_offers(arguments: product_id={{"$from": "step_1.products.0.id"}}, quantity=1, objective="CHEAPEST")
   - step_3: create_purchase_draft(arguments: items={{"$from": "step_2", "$transform": "plan_to_draft_items"}})
5. "Siparişi onaylıyorum" or "Evet" (when PENDING_DRAFT_ID exists) -> goal: ORDER, steps:
   - step_1: place_order(arguments: draft_id={{"$from_context": "pending_draft_id"}})
6. "Stokta olmayanlar için satın alma planı hazırla" -> goal: PLAN, steps:
   - step_1: list_out_of_stock
   - step_2: create_procurement_plan(arguments: items={{"$from": "step_1.products", "$transform": "out_of_stock_products_to_items"}}, objective="CHEAPEST")
7. "bunlardan kaç tane sipariş etmeliyim" (after listing products) -> goal: REASON, steps:
   - step_1: get_stock_replenishment_needed(arguments: product_ids={{"$from_context": "last_reference.id"}})
8. "kanka sadece sayı ver" (follow-up formatting) -> goal: CHAT, answer: "Galaxy S24: 8 adet, A4 Fotokopi Kağıdı: 30 adet"
9. "planı siparişe çevirelim" or "bunların hepsini satın al" (when a procurement plan exists in context/history) -> goal: DRAFT, steps:
   - step_1: create_purchase_draft(arguments: items={{"$from_context": "last_plan", "$transform": "plan_to_draft_items"}})
10. "Elektronik kategorisindeki kritik ürünleri puanı 4.5 üzerindeki satıcılardan satın al" -> goal: DRAFT, steps:
    - step_1: calculate_replenishment(arguments: category="Elektronik")
    - step_2: create_procurement_plan(arguments: items={{"$from": "step_1.replenishments", "$transform": "replenishments_to_items"}}, objective="CHEAPEST", filters={{"min_rating": 4.5}})
    - step_3: create_purchase_draft(arguments: items={{"$from": "step_2", "$transform": "plan_to_draft_items"}})
11. "Stokta azalan ürünleri bul ve en ucuz tekliften taslak sipariş oluştur" -> goal: DRAFT, steps:
    - step_1: list_low_stock
    - step_2: create_procurement_plan(arguments: items={{"$from": "step_1.products", "$transform": "low_stock_products_to_items"}}, objective="CHEAPEST")
    - step_3: create_purchase_draft(arguments: items={{"$from": "step_2", "$transform": "plan_to_draft_items"}})
12. "İki planı karşılaştır, farklar nedir?" (when both cheapest and fastest plans are cached) -> goal: REASON, steps: [], context_sources: ["last_cheapest_plan", "last_fastest_plan"]

DYNAMIC REFERENCING: {{"$from": "step_id.path", "$transform": "replenishments_to_items"|"plan_to_draft_items"|"out_of_stock_products_to_items"|"low_stock_products_to_items"}} or {{"$from_context": "last_reference.id"}} or {{"$from_context": "pending_draft_id"}}

RULES:
1. Omit optional params not asked. Do not pass null/empty/0 unless requested.
2. NEVER place_order unless explicitly requested to order/finalize/approve.
3. For REASON, retrieve missing facts with tools; don't assume.
4. "kritik", "critical", "azalan", "low stock", "stokta olmayan", "out of stock" are NOT categories. Never pass them as 'category' parameter.
5. Reference paths: calculate_replenishment/get_stock_replenishment_needed output is "replenishments". list_out_of_stock/list_low_stock/search_products/list_products output is "products".
6. Use calculate_replenishment/get_stock_replenishment_needed for low/critical/replenishment data retrieval (not list_low_stock/list_products).
7. calculate_replenishment does not take a products parameter. For out-of-stock, use list_out_of_stock -> create_procurement_plan with out_of_stock_products_to_items. For low stock, use list_low_stock -> create_procurement_plan with low_stock_products_to_items.
8. To decide needed quantity, include calculate_replenishment/get_stock_replenishment_needed.
9. Project fields like: step_1.products.id or last_reference.id.
10. FOLLOW-UPS: If the user request is a follow-up asking for specific formatting, summary, simplification, translation, or extracting values (like "sadece sayı ver", "sadece rakamları söyle", "tablo yap", "özetle") from the previous message/tool outputs, set goal="CHAT", do NOT run any tools, and answer directly in "answer" field using the history.
11. QUANTITY VS PLAN: For questions like "how many to order" or "what quantity is needed" (e.g. "kaç tane sipariş etmeliyim", "ne kadar almalıyım", "bunlardan kaç adet sipariş edilmelidir"), set goal="REASON" to provide a natural, conversational sentence answer, and only fetch replenishment quantities. Do NOT call create_procurement_plan/compare_offers unless user explicitly asks for offers, sellers, or optimized plan.
12. PARAMETER KEY: You must ALWAYS use "arguments" as the key for tool parameters inside steps. NEVER use "params".
13. FILTERS: For filtering offers (e.g. in create_procurement_plan or compare_offers), place filter parameters inside the 'filters' object (e.g. {{"filters": {{"min_rating": 4.5}}}}). Do NOT put filter parameters like 'min_rating', 'max_delivery_days', 'max_unit_price', or 'max_shipping_cost' directly in the root of the arguments. Omit the 'filters' parameter entirely if no filters are specified.
14. FILTER PLACEMENT RULES:
    - Product category filters belong to stock/replenishment tools (e.g., use the 'category' parameter in calculate_replenishment, e.g. calculate_replenishment(category="Elektronik")).
    - Seller rating/delivery/pricing filters (like min_rating, max_delivery_days, max_unit_price, max_shipping_cost) belong ONLY to marketplace tools (e.g., filters.min_rating in create_procurement_plan or compare_offers).
    - NEVER pass min_rating, max_delivery_days, max_unit_price, max_shipping_cost, or the 'filters' object to stock tools (e.g., calculate_replenishment, get_stock_replenishment_needed, list_low_stock, list_out_of_stock).
    - Never pass category_name or category to create_procurement_plan or compare_offers.
    - Use only exact argument names defined in each tool schema.
    - Do not move an invalid argument to another tool unless that tool schema explicitly supports it.
    - For category filtering of low/critical/replenishment stock, use calculate_replenishment(category="Elektronik") instead of list_low_stock.
15. DRAFT INTENT RULE:
    If the user explicitly asks to create a draft (e.g., "taslak sipariş oluştur", "taslak oluştur", "create a draft"), set goal="DRAFT", regardless of whether the request also asks to find low-stock products, compare offers, or build a procurement plan first. The final step MUST call create_purchase_draft. A preceding create_procurement_plan step does not make the goal PLAN when draft creation is requested.
16. PURCHASE CONFIRMATION RULE:
    If the user request is to buy, purchase, convert plan to order, or place order (e.g., "bunları satın al", "bu planı satın al", "siparişe dönüştür", "satın alma işlemini başlat") AND a procurement plan exists in context (e.g. LAST_PLAN or last_cheapest_plan or last_fastest_plan):
    - Set goal="DRAFT" (NOT "ORDER").
    - Do NOT call place_order in this plan.
    - Create the draft using create_purchase_draft, converting the cached plan using {{"$from_context": "last_plan", "$transform": "plan_to_draft_items"}}.
    - The system will then format the draft, save pending_draft_id, and ask the user to confirm.
17. ORDER PLACEMENT RULE:
    Only when the user explicitly confirms the draft order (e.g., "onaylıyorum", "evet", "devam et", "siparişi tamamla", "evet, siparişi ver") AND PENDING_DRAFT_ID is present:
    - Set goal="ORDER".
    - Call place_order using draft_id from pending_draft_id, like {{"$from_context": "pending_draft_id"}}.
18. COMPARISON RULE:
    If both LAST_CHEAPEST_PLAN and LAST_FASTEST_PLAN are present in the cached context, and the user asks to compare them, ask which one is better/more logical, or ask about the differences between them (e.g., "ikisini karşılaştır", "hangisi daha mantıklı", "farkları ne"):
    - Set goal="REASON".
    - Set steps=[] (empty list).
    - Set context_sources=["last_cheapest_plan", "last_fastest_plan"].
    - DO NOT run any tools (no steps).
19. NO FINAL_RESPONSE:
    NEVER generate or include the "final_response" field in your JSON output. The host system will construct the final response from execution results.
20. LANGUAGE RULE:
    The "answer" and "final_response" fields (including in the assistant history) MUST always be in Turkish. Never generate or output Chinese, English, or any other language for these fields.
21. ORDER GOAL STEPS RULE:
    For the "ORDER" goal, steps MUST NOT be empty. You must include a step calling the 'place_order' tool with the draft_id argument resolved from pending_draft_id.

CONTEXT VS TOOL:
- If a value is already available in the context (like LAST_CHEAPEST_PLAN, LAST_FASTEST_PLAN, or LAST_REPLENISHMENT), you can refer to it using "$from_context".
- Never use "$from_context" for data produced by an earlier step in the current plan execution. For that, use "$from" referencing the step ID.

Retrieval vs Planning:
- Use data retrieval tools (like list_products, calculate_replenishment, etc.) to get details about stock levels or replenishment needs before planning or ordering.

OUTPUT FORMAT:
Output EXACTLY a single JSON object. No markdown code blocks, no text before/after JSON.
NEVER include "final_response" key in your JSON.

Example output:
{{
  "type": "execution_plan",
  "goal": "CHAT",
  "answer": "Answer here..."
}}
"""
