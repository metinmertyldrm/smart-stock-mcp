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
    """Keep only schema fields that help the planner build valid arguments."""
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = {}
    for key, item in value.items():
        if key not in SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(item, dict):
            result[key] = {name: compact_schema(prop) for name, prop in item.items()}
        else:
            result[key] = compact_schema(item)
    return result


def compact_plan_for_prompt(plan):
    """Reduce cached plans to the fields useful for a follow-up decision."""
    if not plan:
        return None

    if not isinstance(plan, dict):
        if hasattr(plan, "result") and hasattr(plan, "objective"):
            plan_dict = {
                "objective": getattr(plan, "objective", None),
                "items": getattr(plan, "items", None),
                "result": getattr(plan, "result", None),
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
        for key in ("success", "complete", "draft_id", "draftId", "id"):
            if key in result:
                compacted_res[key] = result[key]

        items = result.get("items")
        if isinstance(items, list):
            compacted_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                comp_item = {}
                product_id = item.get("product_id") or item.get("productId") or item.get("id")
                quantity = (
                    item.get("quantity")
                    or item.get("requested_quantity")
                    or item.get("required_quantity")
                )
                if product_id is not None:
                    comp_item["product_id"] = product_id
                if quantity is not None:
                    comp_item["quantity"] = quantity
                if comp_item:
                    compacted_items.append(comp_item)
            compacted_res["items"] = compacted_items
        compacted["result"] = compacted_res
    else:
        items = plan_dict.get("items")
        if isinstance(items, list):
            compacted["items"] = [
                {"product_id": item.get("product_id"), "quantity": item.get("quantity")}
                for item in items
                if isinstance(item, dict)
            ]

    return compacted


def get_reasoning_prompt(user_query: str, tool_results: dict) -> str:
    """Build the natural-language reasoning prompt after tools have run."""
    results_str = json.dumps(tool_results, ensure_ascii=False, separators=(",", ":"))
    return f"""Smart Stock karar açıklama katmanısın.
Kullanıcının sorusunu yalnızca verilen araç/bağlam verileriyle Türkçe yanıtla.

SORU:
{user_query}

VERİ:
{results_str}

KURALLAR:
- Veri, fiyat, stok, tarih veya miktar uydurma.
- ARİTMETİK YAPMA: Karşılaştırmalarda varsa `hesaplanan_karsilastirma` değerlerini aynen kullan; yeniden toplama, çıkarma veya yüzde hesabı yapma.
- Sipariş miktarı soruluyorsa replenishmentQuantityNeeded/quantity değerlerini doğrudan belirt.
- İki plan varsa maliyet, teslimat ve satıcı verilerini karşılaştır; veri yoksa tahmin etme.
- Yalnızca {{"answer":"kısa ve doğal Türkçe cevap"}} biçiminde JSON üret.
- `answer` dışında alan, Markdown, ön açıklama veya iç muhakeme yazma.
"""


def get_decision_journal_prompt(
    user_query: str,
    plan: dict,
    tool_traces: list,
    final_answer: str,
    permission_level: str,
) -> str:
    """Build a prompt that translates an execution trace for non-technical readers."""
    context = {
        "user_query": user_query,
        "plan": plan,
        "tool_traces": tool_traces,
        "final_answer": final_answer,
        "permission_level": permission_level,
    }
    context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"""Smart Stock AI karar günlüğünü teknik bilgisi olmayan bir kullanıcı için yaz.
Hedef kitle LLM, MCP, API, JSON veya yazılım geliştirme bilmiyor. Okuyucu "benden ne istendi, sistem neden bu yolu seçti, ne yaptı, ne buldu ve sonuç ne oldu?" sorularını anlayabilmeli.

HAM BAĞLAM:
{context_json}

Yalnızca aşağıdaki yapıda JSON üret:
{{
  "requestSummary":"...",
  "goalTitle":"...",
  "goalExplanation":"...",
  "permissionExplanation":"...",
  "decision":{{"title":"...","description":"..."}},
  "steps":[{{
    "stepId":"...",
    "title":"...",
    "status":"success | failed | skipped | running",
    "toolName":"...",
    "whatItDoes":"...",
    "whyUsed":"...",
    "findings":["..."],
    "decisionImpact":"..."
  }}],
  "warnings":[]
}}

KURALLAR:
- ORDER/PLAN/FULL gibi kodları açıklamasız başlık yapma.
- `whatItDoes` genel görevi, `whyUsed` bu istekteki özel gerekçeyi anlatsın.
- Ham JSON veya Python sözlüğünü findings içine basma.
- id/draftId/totalCost/status/expectedDeliveryDate alanlarını kullanıcı diline çevir.
- Tarih ve tutarları okunabilir Türkçe biçimde yaz. Örn. 305.930,00 TL.
- Başarısız/skipped adımları başarılı gösterme ve olmayan neden uydurma.
- Ürünleri mümkünse ad ve miktarla anlat; anlamsız iç kimlikleri atla.
- Yeni veri, fiyat, miktar, tarih, satıcı, onay veya güvenlik kontrolü uydurma.
- Nihai sonuçta yapılan işlemi ve varsa kullanıcının sonraki aksiyonunu açıkça belirt.
- Kısa, profesyonel ve teknik olmayan Türkçe kullan.

Kullanıcı isteği: {user_query}
"""


def _tool_line(tool) -> str:
    schema = compact_schema(getattr(tool, "inputSchema", None))
    if isinstance(schema, dict):
        schema.pop("additionalProperties", None)
        if schema.get("type") == "object":
            schema = schema.get("properties", {})
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    description = " ".join((getattr(tool, "description", "") or "").split())
    if len(description) > 160:
        description = description[:157] + "..."
    suffix = f" - {description}" if description else ""
    return f"- {tool.name}{suffix} | args={schema_json}"


def _context_block(last_successful_plan, state, valid_cached_plans) -> str:
    lines = []
    if last_successful_plan:
        lines.append(
            "LAST_PLAN="
            + json.dumps(compact_plan_for_prompt(last_successful_plan), ensure_ascii=False, separators=(",", ":"))
        )

    for name, plan in (valid_cached_plans or {}).items():
        if plan:
            lines.append(
                f"{name.upper()}="
                + json.dumps(compact_plan_for_prompt(plan), ensure_ascii=False, separators=(",", ":"))
            )

    if state:
        if getattr(state, "last_reference_id", None):
            ref_id = state.last_reference_id
            ref_data = state.references.get(ref_id)
            if ref_data:
                reference_line = (
                    f"LAST_REFERENCE=id:{ref_id},type:{ref_data['type']},"
                    f"source:{ref_data['source_tool']},count:{ref_data['count']}"
                )
                reference_data = ref_data.get("data")
                if ref_data.get("source_tool") == "search_offers" and isinstance(reference_data, dict):
                    query = reference_data.get("query")
                    if query:
                        reference_line += f",query:{json.dumps(query, ensure_ascii=False)}"
                lines.append(reference_line)
        if getattr(state, "pending_draft_id", None):
            lines.append(f"PENDING_DRAFT_ID={state.pending_draft_id}")
        if getattr(state, "pending_receive_ids", None):
            lines.append(f"PENDING_RECEIVE_IDS={state.pending_receive_ids}")

    return "\n".join(lines) if lines else "(none)"


def get_execution_plan_prompt(
    tools_list: list,
    last_successful_plan: dict | None = None,
    state=None,
    valid_cached_plans: dict | None = None,
) -> str:
    """Build a compact execution-planning prompt with host safety rules intact."""
    tools_str = "\n".join(_tool_line(tool) for tool in tools_list)
    context = _context_block(last_successful_plan, state, valid_cached_plans)

    return f"""Smart Stock & Procurement execution planner.
Return EXACTLY one JSON object; no Markdown or prose outside JSON.

TOOLS (use only exact names/argument keys shown here):
{tools_str}

CONTEXT:
{context}

OUTPUT:
{{"type":"execution_plan","goal":"CHAT|REASON|INFO|PLAN|DRAFT|ORDER|RECEIVE","steps":[]}}
CHAT uses `answer` and MUST have no steps. Never emit `final_response`. Use `arguments`, never `params`.

GOALS:
- CHAT: formatting/summary/translation/simple follow-up from conversation history; no tools.
- INFO: read-only retrieval only.
- REASON: advice/comparison requiring facts; no write tools.
- PLAN: procurement planning; final step must be create_procurement_plan.
- DRAFT: create a purchase draft; final step must be create_purchase_draft. It never calls place_order.
- ORDER: explicit confirmation of an existing pending draft only.
- RECEIVE: receiving stock is two-phase and requires explicit confirmation.

NON-NEGOTIABLE SAFETY / ROUTING RULES:
1. Use only tool arguments present in each tool schema. Omit optional values not requested; do not send null/empty/0 merely as placeholders.
2. INFO and REASON are read-only. Never use create_purchase_draft, place_order, create_incoming_order(s), or receive_order(s) for them.
3. DRAFT INTENT RULE: if the user explicitly asks for a draft/taslak, goal=DRAFT even if earlier steps retrieve stock or create a procurement plan. A preceding create_procurement_plan step does not make the goal PLAN when draft creation is requested.
4. A request to buy/purchase/convert a plan, when a procurement plan already exists, creates a DRAFT first. Do not place_order yet. Use create_purchase_draft with {{"$from_context":"last_plan","$transform":"plan_to_draft_items"}} when LAST_PLAN is the source.
5. ORDER is allowed only after explicit user confirmation AND PENDING_DRAFT_ID exists. Required chain: place_order(draft_id={{"$from_context":"pending_draft_id"}}) then create_incoming_orders(items={{"$from":"step_1","$transform":"order_to_incoming_items"}}).
6. RECEIVE is also confirmation-gated. First request: RECEIVE with only list_incoming_orders(pending_only=true,ready_only=true), then stop. Only after explicit confirmation AND PENDING_RECEIVE_IDS exists may receive_orders(order_ids={{"$from_context":"pending_receive_ids"}}) run. Viewing pending deliveries alone is INFO.
7. Quantity questions such as "kaç tane almalıyım/sipariş etmeliyim" use REASON plus replenishment retrieval. Do not create offers/plans unless explicitly requested.
8. `kritik`, `critical`, `azalan`, `low stock`, `stokta olmayan`, `out of stock` are stock states, NOT categories. Category filters belong to stock/replenishment tools.
9. Seller/marketplace filters (min_rating, max_delivery_days, max_unit_price, max_shipping_cost, max_total_budget) belong inside marketplace `filters`. Total budget maps to filters.max_total_budget, never max_unit_price. Never pass category/category_name to marketplace planning tools.
10. For "toplam bütçe ... eksik ürünleri tamamla", NEVER invent a product_id. Use calculate_replenishment with empty arguments, then create_procurement_plan with all step_1.replenishments transformed by replenishments_to_items and filters.max_total_budget set to the requested total TL budget.
11. For low/critical replenishment data use calculate_replenishment/get_stock_replenishment_needed. For out-of-stock plan: list_out_of_stock -> create_procurement_plan with out_of_stock_products_to_items. For low-stock plan: list_low_stock -> create_procurement_plan with low_stock_products_to_items.
11. Current-plan references use {{"$from":"step_id.path"}}. Conversation context uses {{"$from_context":"name.path"}}. Never put last_reference/last_plan/etc. inside `$from`.
12. Valid context roots: last_plan,last_cheapest_plan,last_fastest_plan,last_product,last_replenishment,last_reference,pending_draft_id,pending_receive_ids. Only declare context_sources that actually exist in CONTEXT.
13. calculate_replenishment/get_stock_replenishment_needed results expose `replenishments`; product listing/search tools expose `products`.
14. If both cached cheapest and fastest procurement plans exist and the user asks to compare them: goal=REASON, steps=[], context_sources=["last_cheapest_plan","last_fastest_plan"]. If missing, retrieve replenishment once and create CHEAPEST and FASTEST plans as REASON steps.
15. If LAST_REFERENCE has source:search_offers and the user asks to compare the cheapest and fastest prior offers, call search_offers again with its recorded query. This refreshes marketplace data through MCP; goal=REASON.
16. `answer` must be Turkish. Do not invent tool results or bypass host confirmation rules.

REFERENCE TRANSFORMS:
- replenishments_to_items
- out_of_stock_products_to_items
- low_stock_products_to_items
- plan_to_draft_items
- order_to_incoming_items

KEY EXAMPLES:
1. "Stokta olmayan ürünleri listele." -> {{"type":"execution_plan","goal":"INFO","steps":[{{"id":"step_1","tool":"list_out_of_stock","arguments":{{}}}}]}}
2. "Kritik ürünler için satın alma planı hazırla" -> goal=PLAN: calculate_replenishment; then create_procurement_plan(items={{"$from":"step_1.replenishments","$transform":"replenishments_to_items"}},objective="CHEAPEST").
3. "Stokta olmayanlar için satın alma planı hazırla" -> goal=PLAN: list_out_of_stock; then create_procurement_plan(items={{"$from":"step_1.products","$transform":"out_of_stock_products_to_items"}},objective="CHEAPEST").
4. "Stokta azalan ürünleri bul ve en ucuz tekliften taslak sipariş oluştur" -> goal: DRAFT; list_low_stock; create_procurement_plan(items={{"$from":"step_1.products","$transform":"low_stock_products_to_items"}},objective="CHEAPEST"); create_purchase_draft(items={{"$from":"step_2","$transform":"plan_to_draft_items"}}).
5. "bunlardan kaç tane sipariş etmeliyim" after a product list -> goal=REASON: get_stock_replenishment_needed(product_ids={{"$from_context":"last_reference.id"}}).
6. "kanka sadece sayı ver" as a formatting follow-up -> goal=CHAT, steps=[], answer in Turkish from history.
7. "Siparişi onaylıyorum" with PENDING_DRAFT_ID -> goal=ORDER with the exact two-step chain in rule 5.
8. "Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle" -> goal=RECEIVE with ONLY list_incoming_orders(pending_only=true,ready_only=true); wait for confirmation before receive_orders.
9. "Toplam bütçe 50.000 TL'yi geçmeyecek şekilde eksik ürünleri tamamla" -> goal=PLAN: calculate_replenishment(arguments={{}}); then create_procurement_plan(items={{"$from":"step_1.replenishments","$transform":"replenishments_to_items"}},objective="CHEAPEST",filters={{"max_total_budget":50000}}).

Return only the execution_plan JSON object.
"""