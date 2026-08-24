import json
import os
import re
import unicodedata

import requests


FAST_INFO_BLOCKERS = (
    "plan",
    "taslak",
    "siparis",
    "satin al",
    "teklif",
    "fiyat",
    "karsilastir",
    "kac tane",
    "ne kadar",
    "almaliyim",
    "onay",
    "teslim",
    "stoga al",
    "purchase",
    "draft",
    "order",
    "compare",
    "cheapest",
    "fastest",
)
READ_ONLY_NEGATIONS = (
    "henuz siparis olusturma",
    "henuz taslak veya siparis olusturma",
    "henuz taslak ya da siparis olusturma",
    "do not create an order",
    "do not place an order",
)
FAST_OFFER_ROUTE_PREFIX = "search_offers:"
OUT_OF_STOCK_TERMS = (
    "stokta olmayan",
    "stok yok",
    "stok miktari sifir",
    "stoku sifir",
    "sifir stok",
    "tukenen",
    "tukenmis",
    "out of stock",
)
LOW_STOCK_TERMS = (
    "kritik stok",
    "kritik urun",
    "azalan stok",
    "azalan urun",
    "dusuk stok",
    "low stock",
    "minimum stok",
)
SAFE_DRAFT_ROUTE = "safe_replenishment_draft"
SAFE_DRAFT_SCOPES = (
    "eksik stok",
    "eksik urun",
    "stokta olmayan",
    "kritik stok",
    "kritik urun",
    "dusuk stok",
)
SAFE_DRAFT_ADVANCED_BLOCKERS = (
    "butce",
    "kategori",
    "teslimat",
    "puan",
    "teklif",
    "en ucuz",
    "en hizli",
    "en yuksek",
    "balanced",
    "cheapest",
    "fastest",
    "onay",
    "confirm",
    "taslak",
    "draft",
)


def _normalize_for_route(text):
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.replace("ı", "i")


def _fast_offer_query(user_message, normalized):
    """Extract a product query from an explicit read-only offer comparison."""
    if not any(term in normalized for term in ("marketplace", "pazaryeri")):
        return None
    if "teklif" not in normalized:
        return None
    if not any(term in normalized for term in ("karsilastir", "listele")):
        return None

    blocker_text = normalized
    for phrase in READ_ONLY_NEGATIONS:
        blocker_text = blocker_text.replace(phrase, "")
    if any(
        term in blocker_text
        for term in ("taslak", "siparis", "satin al", "purchase", "draft", "order")
    ):
        return None

    parts = re.split(r"\s+için\s+", (user_message or "").strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    query = parts[0].strip()
    return query or None


def _fast_read_only_tool(user_message):
    """Return a safe single read tool for unambiguous stock-state retrievals."""
    normalized = _normalize_for_route(user_message)
    offer_query = _fast_offer_query(user_message, normalized)
    if offer_query:
        return FAST_OFFER_ROUTE_PREFIX + offer_query

    blocker_text = normalized
    for phrase in READ_ONLY_NEGATIONS:
        blocker_text = blocker_text.replace(phrase, "")
    if any(term in blocker_text for term in FAST_INFO_BLOCKERS):
        return None
    if any(term in normalized for term in OUT_OF_STOCK_TERMS):
        return "list_out_of_stock"
    if any(term in normalized for term in LOW_STOCK_TERMS):
        return "list_low_stock"
    return None


def _fast_safe_draft_route(user_message):
    """Recognize only a narrow, filter-free replenishment write request.

    The route never places an order. It deterministically builds a DRAFT plan
    whose first step is read-only replenishment calculation. If there is no
    inventory need, the executor's empty-input guard stops the chain before any
    write tool can run. Advanced procurement constraints stay on the full LLM
    planner because they require semantic argument mapping.
    """
    normalized = _normalize_for_route(user_message)
    if any(term in normalized for term in SAFE_DRAFT_ADVANCED_BLOCKERS):
        return None
    if not any(term in normalized for term in SAFE_DRAFT_SCOPES):
        return None
    if "siparis" not in normalized:
        return None
    if not any(term in normalized for term in ("olustur", "hazirla", "ver")):
        return None
    return SAFE_DRAFT_ROUTE


def prepare_inference_messages(messages):
    """Preclassify only execution-planner requests with deterministic safe plans.

    Host-side plan validation, write permissions and MCP execution remain
    authoritative. Simple stock lookups and a narrow first-turn replenishment
    draft request can bypass Ollama entirely; complex procurement, confirmation,
    filtering and reasoning requests keep the full planner prompt.
    """
    if not messages:
        return messages, None

    system = next((item for item in messages if item.get("role") == "system"), None)
    if not system or "Smart Stock & Procurement execution planner." not in system.get("content", ""):
        return messages, None

    user = next((item for item in reversed(messages) if item.get("role") == "user"), None)
    if not user:
        return messages, None

    user_content = user.get("content", "")
    route = _fast_read_only_tool(user_content)
    if route:
        compact_system = f"""Smart Stock read-only execution planner.
This request was preclassified as a simple stock-state lookup.
Return EXACTLY one JSON object and no Markdown/prose:
{{"type":"execution_plan","goal":"INFO","steps":[{{"id":"step_1","tool":"{route}","arguments":{{}}}}]}}
Rules:
- Use exactly `{route}` and no other tool.
- Goal must be INFO and arguments must be an empty object.
- Never emit write tools, procurement plans, drafts, orders, receive actions, `params`, or `final_response`.
- Do not invent data; this response only selects the read tool. Actual data comes from MCP execution.
"""
        return [
            {"role": "system", "content": compact_system},
            {"role": "user", "content": user_content},
        ], route

    route = _fast_safe_draft_route(user_content)
    if route:
        return [
            {
                "role": "system",
                "content": (
                    "Smart Stock deterministic safe draft planner. "
                    "The host will create a replenishment DRAFT plan only; "
                    "place_order is forbidden until a later explicit confirmation."
                ),
            },
            {"role": "user", "content": user_content},
        ], route

    return messages, None


def _fast_execution_plan(route):
    """Build an allow-listed deterministic plan for a preclassified route."""
    if route.startswith(FAST_OFFER_ROUTE_PREFIX):
        query = route.removeprefix(FAST_OFFER_ROUTE_PREFIX)
        plan = {
            "type": "execution_plan",
            "goal": "REASON",
            "steps": [
                {
                    "id": "step_1",
                    "tool": "search_offers",
                    "arguments": {"query": query},
                }
            ],
        }
    elif route == SAFE_DRAFT_ROUTE:
        plan = {
            "type": "execution_plan",
            "goal": "DRAFT",
            "steps": [
                {
                    "id": "step_1",
                    "tool": "calculate_replenishment",
                    "arguments": {},
                },
                {
                    "id": "step_2",
                    "tool": "create_procurement_plan",
                    "arguments": {
                        "items": {
                            "$from": "step_1.replenishments",
                            "$transform": "replenishments_to_items",
                        },
                        "objective": "CHEAPEST",
                    },
                },
                {
                    "id": "step_3",
                    "tool": "create_purchase_draft",
                    "arguments": {
                        "items": {
                            "$from": "step_2",
                            "$transform": "plan_to_draft_items",
                        }
                    },
                },
            ],
        }
    else:
        plan = {
            "type": "execution_plan",
            "goal": "INFO",
            "steps": [
                {
                    "id": "step_1",
                    "tool": route,
                    "arguments": {},
                }
            ],
        }
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":"))


class LLMService:
    def __init__(self):
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
        # Ollama varsayilan num_ctx degeri sistem promptumuzdan kucuk olabilir.
        # Asildiginda prompt BASTAN kesilir; ilk kesilen bolum AVAILABLE TOOLS olur.
        self.num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
        self.num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "256"))
        # Keep network limits configurable, but do not hide prompt/model performance
        # problems behind ever-growing defaults. Slower environments can override them.
        self.connect_timeout = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "20"))
        self.read_timeout = float(os.getenv("OLLAMA_READ_TIMEOUT", "300"))
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("OLLAMA_CONNECT_TIMEOUT and OLLAMA_READ_TIMEOUT must be positive")

    def generate(self, messages, json_mode=False, allow_fast_route=True):
        if allow_fast_route:
            messages, fast_route = prepare_inference_messages(messages)
            if fast_route:
                print(f"[LLM] deterministic planner bypass: {fast_route} (Ollama skipped)")
                return _fast_execution_plan(fast_route)

        prompt_parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            prompt_parts.append(f"{role}: {content}")

        # Qwen3 releases differ in how reliably they honor the API-level
        # think=false flag. The prompt command is a compatible second guard
        # that prevents hidden reasoning from leaking into the user response.
        prompt = "/no_think\n" + "\n".join(prompt_parts)
        if not prompt.endswith("\nassistant:"):
            prompt += "\nassistant:"

        print(f"Prompt karakter sayısı: {len(prompt)}")
        print(f"Prompt token sayısı (tahmini): {len(prompt) // 4}")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx
            }
        }
        if json_mode:
            # Ollama JSON mode constrains Qwen's visible response to a parseable
            # envelope instead of allowing chain-of-thought prose to leak.
            payload["format"] = "json"

        try:
            response = requests.post(
                self.url,
                json=payload,
                timeout=(self.connect_timeout, self.read_timeout)
            )
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama zaman aşımı: connect={self.connect_timeout}s, read={self.read_timeout}s"
            ) from exc

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(
                f"LLM API hatası: {response.status_code} - {response.text}"
            ) from exc

        data = response.json()

        prompt_tokens = data.get("prompt_eval_count")
        if prompt_tokens is not None:
            print(
                f"[LLM] prompt {prompt_tokens} token / num_ctx {self.num_ctx} | "
                f"cikti {data.get('eval_count')} token / num_predict {self.num_predict}"
            )
            if prompt_tokens >= self.num_ctx:
                print(
                    "[LLM] UYARI: prompt baglam penceresini doldurdu. "
                    "Prompt bastan kesilmis olabilir (once AVAILABLE TOOLS gider)."
                )

        if "response" not in data:
            raise RuntimeError(f"Ollama cevabında 'response' alanı yok: {data}")
        return data["response"]
