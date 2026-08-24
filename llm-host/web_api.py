"""Persistent HTTP transport for the Smart Stock agent."""
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import (MARKETPLACE_SERVER_PATH, STOCK_SERVER_PATH, CachedProcurementPlan,
                 ConversationState, clean_tool_results_for_reasoning, execute_plan,
                 format_final_answer, format_order_confirmation,
                 format_procurement_plan, format_purchase_draft, format_receive_proposal,
                 format_received_orders,
                 build_repair_instruction, get_execution_plan_prompt, is_plan_valid,
                 parse_execution_plan,
                 resolve_step_arguments, validate_plan_against_state)
from llm import LLMService
from mcp_client import MCPClient
from prompt import get_reasoning_prompt

WRITE_TOOLS = {"create_purchase_draft", "place_order", "create_incoming_order",
               "create_incoming_orders", "receive_order", "receive_orders"}
WRITE_INTENT_WORDS = ("sipariş", "taslak", "satın al", "oluştur", "draft", "order",
                      "stoğa al", "stoga al", "teslim al", "receive")
NEGATED_WRITE_PHRASES = (
    "henüz sipariş oluşturma",
    "henuz siparis olusturma",
    "henüz taslak veya sipariş oluşturma",
    "henuz taslak veya siparis olusturma",
    "henüz taslak ya da sipariş oluşturma",
    "henuz taslak ya da siparis olusturma",
    "do not create an order",
    "do not place an order",
)
CONFIRM_INTENT_WORDS = ("evet", "onay", "onayla", "onaylıyorum", "devam", "tamam", "yes", "confirm")


def strip_negated_write_phrases(message):
    normalized = (message or "").casefold()
    for phrase in NEGATED_WRITE_PHRASES:
        normalized = normalized.replace(phrase, "")
    return normalized


def has_write_intent(message):
    normalized = strip_negated_write_phrases(message)
    return any(word in normalized for word in WRITE_INTENT_WORDS)


def prior_offer_refresh_plan(message, state):
    """Safe fallback when the model cannot plan an explicit offer follow-up."""
    normalized = strip_negated_write_phrases(message)
    asks_tradeoff = (
        "en ucuz" in normalized
        and ("en hızlı" in normalized or "en hizli" in normalized)
        and ("karşılaştır" in normalized or "karsilastir" in normalized)
    )
    if not asks_tradeoff or not state.last_reference_id:
        return None

    reference = state.references.get(state.last_reference_id)
    if not isinstance(reference, dict) or reference.get("source_tool") != "search_offers":
        return None
    data = reference.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        return None

    query = data.get("query")
    if not query and data["offers"]:
        product = data["offers"][0].get("product")
        if isinstance(product, dict):
            query = product.get("name")
        else:
            query = data["offers"][0].get("productName")
    if not query:
        return None

    return {
        "type": "execution_plan",
        "goal": "REASON",
        "steps": [{
            "id": "step_1",
            "tool": "search_offers",
            "arguments": {"query": query},
        }],
    }


def structured_answer(raw, fallback):
    """Read only the public answer field from Ollama's JSON response."""
    try:
        parsed = json.loads((raw or "").strip())
        answer = parsed.get("answer") if isinstance(parsed, dict) else None
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    logger.warning("Ollama final answer was not a valid answer envelope; using safe formatter")
    return fallback


DB_PATH = os.getenv("LLM_CONVERSATIONS_DB", os.path.join(os.path.dirname(__file__), "conversations.db"))
# Gecmis promptun icine giriyor; num_ctx tasmasin diye hem adet hem uzunluk sinirli.
HISTORY_MESSAGES = int(os.getenv("LLM_HISTORY_MESSAGES", "8"))
HISTORY_CHARS = int(os.getenv("LLM_HISTORY_CHARS", "800"))
logger = logging.getLogger(__name__)

TITLE_STOP_WORDS = {
    "acaba", "bir", "bu", "da", "de", "en", "icin", "için", "ile", "mi", "mı",
    "mu", "mü", "ve", "veya", "lütfen", "lutfen", "bana", "olan", "olarak",
}


def conversation_title(message):
    """Produce a compact, deterministic title when no title model is available."""
    clean = re.sub(r"[\r\n\t]+", " ", message).strip(" .,:;!?-—")
    lowered = strip_negated_write_phrases(clean)
    budget = re.search(r"\b[\d.]+(?:,[\d]+)?\s*(?:TL|₺)\b", clean, re.IGNORECASE)
    if budget:
        return f"{budget.group(0)} Bütçeli Satın Alma"
    patterns = (
        (("bekleyen", "sipariş"), "Bekleyen Siparişleri Kontrol Etme"),
        (("stokta olmayan",), "Eksik Stokları Tamamlama"),
        (("eksik", "ürün"), "Eksik Stokları Tamamlama"),
        (("stok", "plan"), "Stok Planlaması"),
        (("taslak", "sipariş"), "Taslak Sipariş Oluşturma"),
        (("teklif",), "Tedarik Tekliflerini Karşılaştırma"),
    )
    for terms, title in patterns:
        if all(term in lowered for term in terms):
            # Preserve a product/model identifier when one is present.
            model = next((word for word in clean.split() if any(char.isdigit() for char in word)), None)
            if model and title == "Stok Planlaması":
                return f"{model[:24]} Stok Planlaması"
            return title
    words = [word for word in re.findall(r"[\wÇĞİÖŞÜçğıöşü.-]+", clean, re.UNICODE)
             if word.casefold() not in TITLE_STOP_WORDS]
    selected = words[:6]
    return " ".join(selected).title()[:100] or "Yeni sohbet"


def now():
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS conversations_owner_updated
            ON conversations(owner_id, updated_at DESC);
          CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, status TEXT NOT NULL, response_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
          );
        """)
        self.db.commit()

    def _owned(self, conversation_id, owner_id):
        row = self.db.execute("SELECT * FROM conversations WHERE id=? AND owner_id=?", (conversation_id, owner_id)).fetchone()
        if not row:
            raise HTTPException(404, "Sohbet bulunamadı.")
        return row

    def create(self, owner_id, title="Yeni sohbet", conversation_id=None):
        conversation_id = conversation_id or str(uuid.uuid4())
        timestamp = now()
        self.db.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", (conversation_id, owner_id, title, timestamp, timestamp))
        self.db.commit()
        return self.get(conversation_id, owner_id, include_messages=False)

    def ensure(self, conversation_id, owner_id, first_message):
        row = self.db.execute("SELECT owner_id,title FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row and row["owner_id"] != owner_id:
            raise HTTPException(404, "Sohbet bulunamadı.")
        if not row:
            self.create(owner_id, conversation_title(first_message), conversation_id)
        elif row["title"] == "Yeni sohbet" and first_message.strip():
            self.db.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                            (conversation_title(first_message), now(), conversation_id))
            self.db.commit()

    def list(self, owner_id, limit=50, offset=0):
        rows = self.db.execute("SELECT * FROM conversations WHERE owner_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?", (owner_id, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def get(self, conversation_id, owner_id, include_messages=True):
        result = dict(self._owned(conversation_id, owner_id))
        if include_messages:
            rows = self.db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,)).fetchall()
            result["messages"] = [{"id": r["id"], "conversationId": r["conversation_id"], "role": r["role"], "content": r["content"], "status": r["status"], "createdAt": r["created_at"], "response": json.loads(r["response_json"]) if r["response_json"] else None} for r in rows]
        return result

    def add_message(self, conversation_id, role, content, status="success", response=None):
        timestamp = now()
        self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), conversation_id, role, content, status, json.dumps(response, ensure_ascii=False) if response else None, timestamp))
        self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id))
        self.db.commit()

    def history(self, conversation_id, limit=HISTORY_MESSAGES):
        rows = self.db.execute("SELECT role,content FROM messages WHERE conversation_id=? AND status='success' ORDER BY created_at DESC LIMIT ?", (conversation_id, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"][:HISTORY_CHARS]} for r in reversed(rows)]

    def pending_draft(self, conversation_id):
        rows = self.db.execute(
            "SELECT response_json FROM messages WHERE conversation_id=? AND response_json IS NOT NULL ORDER BY created_at DESC,id DESC",
            (conversation_id,),
        ).fetchall()
        for row in rows:
            response = json.loads(row["response_json"])
            if "pendingDraftId" in response:
                return response["pendingDraftId"]
        return None

    def delete(self, conversation_id, owner_id):
        self._owned(conversation_id, owner_id)
        self.db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        self.db.commit()

    def close(self):
        self.db.close()


class ChatRequest(BaseModel):
    conversationId: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)


class CreateConversationRequest(BaseModel):
    title: str = Field(default="Yeni sohbet", min_length=1, max_length=100)


def summary(value):
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text[:300] + ("…" if len(text) > 300 else "")


TOOL_EXPLANATIONS = {
    "list_low_stock": ("Kritik stokları kontrol et", "Minimum stok seviyesinin altındaki ürünleri belirlemek için çağrıldı."),
    "list_out_of_stock": ("Tükenen stokları kontrol et", "Stokta kalmayan ürünleri belirlemek için çağrıldı."),
    "calculate_replenishment": ("Eksik miktarları hesapla", "Her ürünün hedef stok seviyesine ulaşması için gereken miktarı hesaplamak amacıyla çağrıldı."),
    "compare_offers": ("Teklifleri karşılaştır", "Uygun satıcı tekliflerini maliyet ve teslimat koşullarına göre karşılaştırmak için çağrıldı."),
    "create_procurement_plan": ("Satın alma planını oluştur", "Gereken miktarları seçilen optimizasyon hedefine göre karşılayan teklif kombinasyonunu oluşturmak için çağrıldı."),
    "create_purchase_draft": ("Sipariş taslağı oluştur", "Seçilen satın alma planını kullanıcı onayından önce taslağa dönüştürmek için çağrıldı."),
    "place_order": ("Siparişi ver", "Kullanıcının onayladığı taslağı gerçek siparişe dönüştürmek için çağrıldı."),
    "list_incoming_orders": ("Bekleyen teslimatları kontrol et", "Teslim alınabilecek bekleyen siparişleri belirlemek için çağrıldı."),
    "receive_orders": ("Ürünleri stoğa al", "Kullanıcının onayladığı teslimatların stok miktarlarına işlenmesi için çağrıldı."),
}
FALLBACK_PURPOSE = "Bu adım planın bir sonraki işlemi için gerekli veriyi sağlamak üzere çalıştırıldı."
SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "apikey", "credential", "authorization",
                  "card_number", "iban", "cvv", "system_prompt", "thinking"}


def safe_value(value, depth=0):
    """Return bounded, display-safe structured data; never expose secret-like fields."""
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {str(k): "[gizlendi]" if any(x in str(k).casefold() for x in SENSITIVE_KEYS)
                else safe_value(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list):
        return [safe_value(v, depth + 1) for v in value[:20]]
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:200]


def result_findings(value):
    if not isinstance(value, dict):
        return [summary(value)] if value not in (None, "") else []
    findings = []
    for key, val in list(value.items())[:8]:
        if isinstance(val, list):
            findings.append(f"{key}: {len(val)} kayıt")
        elif isinstance(val, (str, int, float, bool)) and key.casefold() not in SENSITIVE_KEYS:
            findings.append(f"{key}: {str(val)[:120]}")
    return findings[:5]


class AgentApplication:
    def __init__(self, client, llm, store=None):
        self.client, self.llm = client, llm
        self.store = store or ConversationStore()
        self.states = {}

    async def _generate(self, messages, *, json_mode=False):
        """Run Ollama outside the event loop and never bypass it for web requests."""
        return await asyncio.to_thread(
            self.llm.generate,
            messages,
            json_mode=json_mode,
            allow_fast_route=False,
        )

    async def chat(self, conversation_id, message, owner_id="anonymous"):
        started_at = now()
        started_clock = time.perf_counter()
        execution_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        message = message.strip()
        if not message:
            raise HTTPException(422, "Mesaj boş olamaz.")
        self.store.ensure(conversation_id, owner_id, message)
        state = self.states.setdefault(conversation_id, ConversationState())
        if state.pending_draft_id is None:
            state.pending_draft_id = self.store.pending_draft(conversation_id)
        state.history = self.store.history(conversation_id)
        state.last_user_message = message
        normalized = strip_negated_write_phrases(message)
        write_intent = has_write_intent(message)
        awaiting_confirmation = state.pending_draft_id is not None or bool(state.pending_receive_ids)
        confirm_intent = awaiting_confirmation and any(word in normalized for word in CONFIRM_INTENT_WORDS)
        permission = "FULL" if write_intent or confirm_intent else "PLAN"
        tools = await self.client.list_tools()
        names = {t.name for t in tools}
        cached = {k: v for k, v in {"last_cheapest_plan": state.last_cheapest_plan, "last_fastest_plan": state.last_fastest_plan}.items() if is_plan_valid(v)}
        system_prompt = get_execution_plan_prompt(tools, state.last_plan, state, cached)
        raw = await self._generate(
            [
                {"role": "system", "content": system_prompt},
                *state.history,
                {"role": "user", "content": message},
            ],
            json_mode=True,
        )
        repaired = False
        repair_summary = None
        try:
            plan = parse_execution_plan(raw)
            validate_plan_against_state(plan, state)
        except ValueError as exc:
            # Model çağrısı her zaman önce yapılır. Bilinen bir teklif takip isteğinde
            # bozuk JSON güvenli bir read-only MCP planına düşer; diğer istekler normal
            # model onarım akışını kullanır.
            plan = prior_offer_refresh_plan(message, state)
            if plan is None:
                plan = await self.repair_invalid_plan(
                    system_prompt, message, raw, exc, state, conversation_id
                )
            if plan is None:
                return self.clarification_response(conversation_id, message, permission, state, exc)
            repaired = True
            repair_summary = "Model planı doğrulanamadığı için güvenli ve çalıştırılabilir bir planla değiştirildi."
        if permission == "PLAN" and any(s.get("tool") in WRITE_TOOLS for s in plan.get("steps", [])):
            raise HTTPException(403, "Salt okunur istekte yazma işlemi engellendi.")
        self.store.add_message(conversation_id, "user", message)
        execution = await execute_plan(plan, self.client, names, state)
        if not execution.get("success") and execution.get("retryable") is not False:
            # retryable=False: is durumu (or. siparis edilecek urun yok).
            # Yeniden planlamak olmayan veriyi var etmez, kullaniciyi bosuna bekletir.
            previous_tools = [s.get("tool") for s in plan.get("steps", [])]
            new_plan, new_execution = await self.repair(system_prompt, message, plan, execution, names, state, permission, conversation_id)
            if new_execution.get("success") and new_plan is not plan:
                repaired = True
                new_tools = [s.get("tool") for s in new_plan.get("steps", [])]
                repair_summary = f"Başarısız plan düzeltildi. Önceki araçlar: {', '.join(previous_tools)}; yeni araçlar: {', '.join(new_tools)}."
            plan, execution = new_plan, new_execution
        trace = []
        results = execution.get("results", {})
        for index, step in enumerate(plan.get("steps", [])):
            sid = step.get("id") or f"step_{index + 1}"
            failed = execution.get("failed_step") == sid
            executed = sid in results
            # Onceki adim patlayinca sonrakiler hic calismaz; bunlari basarili gostermek yaniltici.
            status = "failed" if failed else "success" if executed else "skipped"
            if failed:
                detail = execution.get("error")
            elif executed:
                detail = results.get(sid)
            else:
                detail = "Önceki adım başarısız olduğu için çalıştırılmadı."
            tool = step.get("tool") or "unknown"
            title, purpose = TOOL_EXPLANATIONS.get(tool, (tool.replace("_", " ").title(), FALLBACK_PURPOSE))
            findings = result_findings(detail) if executed else []
            trace.append({"stepId": sid, "tool": tool, "title": title, "purpose": purpose,
                          "toolCallId": f"{execution_id}:{sid}", "mcpRequestId": f"{trace_id}:{index + 1}",
                          "mcpServer": "smart-stock-mcp", "trigger": "Kullanıcı isteğinden üretilen plan." if index == 0 else f"{plan.get('steps', [])[index - 1].get('id', f'step_{index}')} adımının tamamlanması.",
                          "dependency": None if index == 0 else plan.get("steps", [])[index - 1].get("id", f"step_{index}"),
                          "arguments": safe_value(step.get("arguments", {})),
                          "inputSummary": summary(safe_value(step.get("arguments", {}))), "status": status,
                          "resultSummary": summary(safe_value(detail)), "findings": findings,
                          "interpretation": "Araç sonucu başarıyla alındı." if executed else detail,
                          "impactOnDecision": "Sonuç, sonraki plan adımının girdisini oluşturdu." if executed else "Plan bu adımdan sonra ilerletilemedi.",
                          "nextAction": "Planın sıradaki adımına geçildi." if executed else None,
                          "durationMs": execution.get("durations_ms", {}).get(sid),
                          "retryCount": 1 if repaired else 0, "rawResponse": safe_value(detail),
                          "error": str(execution.get("error"))[:300] if failed else (detail if status == "skipped" else None)})
        final = execution.get("last_result", {})
        last_tool = plan.get("steps", [{}])[-1].get("tool") if plan.get("steps") else None
        if execution.get("success"):
            for index, step in enumerate(plan.get("steps", [])):
                if step.get("tool") != "create_procurement_plan":
                    continue
                step_id = step.get("id") or f"step_{index + 1}"
                result = execution.get("results", {}).get(step_id)
                if not isinstance(result, dict) or result.get("success") is False:
                    continue
                arguments = resolve_step_arguments(step.get("arguments", {}), execution["results"], state)
                cached_plan = CachedProcurementPlan(
                    objective=arguments.get("objective", "CHEAPEST"),
                    items=arguments.get("items", []),
                    result=result,
                    saved_at=now(),
                )
                if cached_plan.objective == "CHEAPEST":
                    state.last_cheapest_plan = cached_plan
                elif cached_plan.objective == "FASTEST":
                    state.last_fastest_plan = cached_plan

        # Zincir create_incoming_orders ile bitebildigi icin "son adim place_order mi"
        # kontrolu yetmiyor; siparis adimini adiyla ariyoruz.
        ordered_step_id = None
        listing_step_id = None
        received_step_id = None
        if execution.get("success"):
            for index, step in enumerate(plan.get("steps", [])):
                step_id = step.get("id") or f"step_{index + 1}"
                tool = step.get("tool")
                if tool == "place_order":
                    ordered_step_id = step_id
                elif tool == "list_incoming_orders":
                    listing_step_id = step_id
                elif tool in ("receive_orders", "receive_order"):
                    received_step_id = step_id

        goal = plan.get("goal", "").upper()
        if execution.get("success") and goal in {"INFO", "REASON"}:
            reasoning_data = clean_tool_results_for_reasoning(execution.get("results", {}))
            raw_answer = await self._generate(
                [{"role": "system", "content": get_reasoning_prompt(message, reasoning_data)}],
                json_mode=True,
            )
            fallback = (
                format_final_answer(final, last_tool)
                if last_tool
                else format_final_answer(reasoning_data)
            )
            answer = structured_answer(raw_answer, fallback)
        elif goal == "CHAT":
            answer = final.get("chat_answer", "")
        elif received_step_id is not None:
            answer = format_received_orders(results.get(received_step_id) or {})
        elif goal == "RECEIVE" and listing_step_id is not None:
            # Oneri asamasi: stok degistirmeden once onay iste.
            answer = format_receive_proposal(results.get(listing_step_id) or {})
        elif ordered_step_id is not None:
            # ORDER zinciri: ozet place_order + create_incoming_orders sonuclarindan kurulur.
            incoming = next((results.get(step.get("id") or f"step_{index + 1}")
                             for index, step in enumerate(plan.get("steps", []))
                             if step.get("tool") == "create_incoming_orders"), None)
            answer = format_order_confirmation(results.get(ordered_step_id) or {}, incoming)
        else:
            answer = format_purchase_draft(final) if last_tool == "create_purchase_draft" else format_procurement_plan(final) if last_tool == "create_procurement_plan" else format_final_answer(final, last_tool)
        if not execution.get("success"):
            # Teknik hata ile is durumunu ayirt et; ham hata karar gunlugunde kaliyor.
            answer = execution.get("business_reason") or (
                "İşlem tamamlanamadı. Lütfen isteğinizi kontrol edip yeniden deneyin."
            )
        # Taslak kimligini yalnizca create_purchase_draft adiminin sonucundan al.
        # Tool MarketplacePurchaseDraftResponse dondurur ve anahtar "id"dir; ama
        # herhangi bir adimdaki "id" alanini kabul etmek tehlikeli olur (place_order
        # da "id" donduruyor, o siparis kimligi).
        draft_id = None
        for index, step in enumerate(plan.get("steps", [])):
            if step.get("tool") != "create_purchase_draft":
                continue
            result = results.get(step.get("id") or f"step_{index + 1}")
            if isinstance(result, dict):
                candidate = result.get("draftId") or result.get("draft_id") or result.get("id")
                if candidate:
                    draft_id = candidate
        if draft_id:
            state.pending_draft_id = int(draft_id)
        if ordered_step_id is not None:
            # Siparis verildi: taslak artik bekleyen degil, onay dugmesi kaybolmali.
            state.pending_draft_id = None

        if goal == "RECEIVE" and listing_step_id is not None and received_step_id is None:
            listing = results.get(listing_step_id) or {}
            state.pending_receive_ids = [
                entry.get("id") for entry in (listing.get("orders") or [])
                if isinstance(entry, dict) and entry.get("id")
            ]
        elif received_step_id is not None:
            state.pending_receive_ids = []
        pending = bool(state.pending_draft_id or state.pending_receive_ids)
        ordered = ordered_step_id is not None
        stock_changed = received_step_id is not None
        safety_checks = [
            {"label": "Salt okunur sınırı", "status": "passed", "detail": "PLAN izninde değişiklik yapan araçlar host tarafından engellenir." if permission == "PLAN" else "FULL işlem sınırları uygulandı."},
            {"label": "Plan doğrulama", "status": "passed" if execution.get("success") else "blocked", "detail": "Plan host kurallarıyla doğrulandı." if execution.get("success") else "Plan güvenli biçimde tamamlanamadı."},
            {"label": "Kullanıcı onayı", "status": "pending" if pending else "passed", "detail": "Kritik işlem kullanıcı onayı bekliyor." if pending else "Bekleyen kullanıcı onayı yok."},
            {"label": "Sipariş durumu", "status": "warning" if ordered else "passed", "detail": "Onaylanan sipariş verildi." if ordered else "Sipariş verilmedi."},
            {"label": "Stok değişikliği", "status": "warning" if stock_changed else "passed", "detail": "Onaylanan teslimatlar stoğa işlendi." if stock_changed else "Stok değiştirilmedi."},
        ]
        all_findings = [finding for item in trace for finding in item.get("findings", [])][:8]
        goal_reasons = [f"Kullanıcı isteği {plan.get('goal', 'PLAN')} işlem hedefiyle eşleşti."]
        if plan.get("steps"):
            goal_reasons.append(f"Hedefi uygulamak için doğrulanmış {len(plan['steps'])} plan adımı üretildi.")
        if execution.get("success"):
            goal_reasons.append("Plan adımları host doğrulamasından geçti ve beklenen sırada çalıştı.")
        changes = []
        for index, step in enumerate(plan.get("steps", [])):
            if step.get("tool") not in WRITE_TOOLS:
                continue
            value = results.get(step.get("id") or f"step_{index + 1}")
            if isinstance(value, dict):
                entity_id = value.get("orderId") or value.get("draftId") or value.get("id")
                changes.append(f"{step.get('tool')} yazma işlemi tamamlandı" + (f"; kayıt #{entity_id}." if entity_id else "; kayıt kimliği sonuçta bulunmuyor."))
        explanation = {
            "requestSummary": message[:240], "originalRequest": message[:4000],
            "detectedIntent": conversation_title(message), "entities": all_findings,
            "missingInformation": [], "ambiguities": [], "assumptions": [],
            "goalTitle": conversation_title(message), "goalReasons": goal_reasons,
            "alternativeExplanation": "Zorunlu plan doğrulaması başarılı olduğu için CLARIFY hedefi gerekmedi." if goal != "CLARIFY" else None,
            "confidence": "Doğrulanmış plan" if execution.get("success") else "Belirsiz — plan tamamlanamadı",
            "goalExplanation": f"{plan.get('goal', 'PLAN')} hedefi için {len(plan.get('steps', []))} adımlı operasyon planı uygulandı.",
            "permissionExplanation": "İstek bilgi alma veya planlama niteliğinde olduğu için değişiklik yapan araçlara izin verilmedi." if permission == "PLAN" else "İstek değişiklik yapan bir işlem içeriyor; kritik adımlar kullanıcı onayı ve host doğrulamasına tabidir.",
            "permissionSource": "Host yazma-niyeti politikası (WRITE_INTENT_WORDS); kalıcı kural kimliği tanımlanmamış.",
            "permissionReason": "Mesajda yazma niyeti tespit edildi." if permission == "FULL" else "Mesajda yazma niyeti tespit edilmedi.",
            "allowedActions": ["Bilgi okuma", "Plan oluşturma"] + (["İzin verilen yazma araçlarını çalıştırma"] if permission == "FULL" else []),
            "blockedActions": [] if permission == "FULL" else ["Sipariş, taslak veya stok kaydı oluşturma/değiştirme"],
            "approvalExplanation": "Kritik yazma işlemi için kullanıcı onayı bekleniyor." if pending else "Bekleyen bir onay adımı yok.",
            "riskLevel": "Orta — kalıcı veri değişikliği" if permission == "FULL" else "Düşük — salt okunur/planlama",
            "findings": all_findings, "decisionSummary": answer[:300], "safetyChecks": safety_checks,
            "changes": changes, "userNextAction": "Onay düğmesini kullanın." if pending else "Ek işlem gerekmiyor.",
            "rollback": "Rollback desteği bu işlem kaydında belirtilmemiş.",
            "warnings": ([] if execution.get("success") else ["Plan tamamlanamadı."]) + (["Kullanıcı onayı bekleniyor."] if pending else []),
            "repaired": repaired,
        }
        if repair_summary:
            explanation["repairSummary"] = repair_summary
        plan["id"] = f"plan:{execution_id}"
        finished_at = now()
        telemetry = {"executionId": execution_id, "traceId": trace_id,
                     "planId": plan["id"], "model": os.getenv("LLM_MODEL", "loglanmamış"),
                     "promptVersion": "execution-plan-v1", "applicationVersion": os.getenv("APP_VERSION", "development"),
                     "environment": os.getenv("APP_ENV", "development"), "startedAt": started_at,
                     "finishedAt": finished_at, "durationMs": round((time.perf_counter() - started_clock) * 1000),
                     "missingFields": ["HTTP request ID", "MCP server sürümü", "tool sürümü", "idempotency key"]}
        response = {"conversationId": conversation_id, "permissionLevel": permission, "plan": plan,
                    "trace": trace, "finalAnswer": answer, "pendingDraftId": state.pending_draft_id,
                    "succeeded": bool(execution.get("success")),
                    "pendingReceiveIds": list(state.pending_receive_ids), "explanation": explanation,
                    "telemetry": telemetry}
        self.store.add_message(conversation_id, "assistant", answer, "success" if execution.get("success") else "failed", response)
        return response

    async def repair_invalid_plan(self, system_prompt, message, raw, error, state, conversation_id):
        """Plan dogrulamadan gecemediginde modele hatayi geri verip bir kez daha dener."""
        logger.info("Plan validation failed (%s); attempting repair conversation_id=%s", error, conversation_id)
        try:
            repaired_raw = await self._generate([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_repair_instruction(
                    message,
                    {"invalid_plan_output": raw},
                    {"success": False, "stage": "plan_validation", "error": str(error)},
                    "",
                )},
            ], json_mode=True)
            repaired = parse_execution_plan(repaired_raw)
            validate_plan_against_state(repaired, state)
            return repaired
        except Exception:
            logger.exception("Invalid plan could not be repaired conversation_id=%s", conversation_id)
            return None

    def clarification_response(self, conversation_id, message, permission, state, reason):
        """Onarim da tutmadi: zinciri durdur, kullaniciya ne yapamadigimizi acikla ve sor.

        Dogrulama hatalarinin metni MODELE yazilmistir (onarim turunda yol gostersin diye).
        Kullaniciya oldugu gibi basmak teknik ve kafa karistirici; burada insana
        yonelik bir karsiligi veriliyor, ham metin ayrica plan detayinda saklaniyor.
        """
        detail = str(reason)
        if "place_order" in detail:
            answer = (
                "Onay bekleyen bir taslak sipariş olmadığı için doğrudan sipariş veremem. "
                "Önce neyi almak istediğinizi söyleyin, taslağı hazırlayayım. "
                "Örneğin: \"Stokta azalan ürünler için en ucuz tekliflerden taslak sipariş oluştur.\" "
                "Taslağı gösterdikten sonra onayınızı isteyeceğim."
            )
        else:
            answer = (
                "İsteğinizi çalıştırılabilir bir işlem planına dönüştüremedim. "
                "Biraz daha belirgin yazar mısınız? Hangi ürün grubunu (stokta olmayanlar, "
                "kritik seviyedekiler, belirli bir kategori), hangi ölçütü (en ucuz, en hızlı, "
                "en yüksek puanlı) ve sonucu satın alma planı olarak mı yoksa taslak sipariş "
                "olarak mı istediğinizi belirtirseniz yardımcı olabilirim."
            )
        self.store.add_message(conversation_id, "user", message)
        response = {
            "conversationId": conversation_id,
            "permissionLevel": permission,
            "plan": {"type": "execution_plan", "goal": "CLARIFY", "steps": [], "detail": detail},
            "trace": [],
            "finalAnswer": answer,
            "pendingDraftId": state.pending_draft_id,
            "succeeded": False,
            "explanation": {
                "originalRequest": message[:4000], "requestSummary": message[:240],
                "detectedIntent": "İsteği netleştir", "entities": [],
                "missingInformation": [answer], "ambiguities": ["İstek güvenli ve çalıştırılabilir bir plana dönüştürülemedi."],
                "assumptions": [], "goalTitle": "Ek bilgi iste", "goalExplanation": "Yanlış veya eksik işlem riskini önlemek için CLARIFY seçildi.",
                "goalReasons": ["Host plan doğrulaması başarısız oldu.", "Otomatik plan onarımı da geçerli bir plan üretemedi."],
                "alternativeExplanation": "ORDER veya başka bir işlem hedefi, gerekli bilgiler doğrulanamadığı için seçilmedi.",
                "confidence": "Düşük — kullanıcı açıklaması gerekli", "permissionExplanation": f"{permission} sınırı korundu; hiçbir tool çalıştırılmadı.",
                "permissionSource": "Host yazma-niyeti politikası", "permissionReason": "İstek netleşene kadar işlem yapılmadı.",
                "allowedActions": ["Kullanıcıdan ek bilgi isteme"], "blockedActions": ["Tool çalıştırma", "Kalıcı veri değişikliği"],
                "approvalExplanation": "Onay değil, açıklayıcı bilgi bekleniyor.", "riskLevel": "Düşük — değişiklik yapılmadı",
                "findings": [], "decisionSummary": answer[:300], "changes": [], "userNextAction": "İstenen ürün, ölçüt ve çıktı türünü belirtin.",
                "rollback": "Değişiklik yapılmadığı için geri alma gerekmiyor.",
                "safetyChecks": [{"label": "Plan doğrulama", "status": "blocked", "detail": "Geçersiz plan güvenli biçimde durduruldu."}],
                "warnings": ["Plan doğrulama ayrıntısı teknik plan.detail alanında saklandı."], "repaired": False,
            },
        }
        self.store.add_message(conversation_id, "assistant", answer, "failed", response)
        return response

    async def repair(self, system_prompt, message, plan, execution, names, state, permission, conversation_id):
        """Basarisiz plani bir kez LLM'e geri verip duzeltilmis planla yeniden dener."""
        logger.info("Plan failed at step=%s error=%s; attempting repair conversation_id=%s",
                    execution.get("failed_step"), execution.get("error"), conversation_id)
        try:
            repaired_raw = await self._generate([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_repair_instruction(message, plan, execution, plan.get("goal", "").upper())},
            ], json_mode=True)
            repaired = parse_execution_plan(repaired_raw)
            validate_plan_against_state(repaired, state)
        except Exception:
            logger.exception("Plan repair could not be parsed conversation_id=%s", conversation_id)
            return plan, execution

        if permission == "PLAN" and any(s.get("tool") in WRITE_TOOLS for s in repaired.get("steps", [])):
            raise HTTPException(403, "Salt okunur istekte yazma islemi engellendi.")

        try:
            repaired_execution = await execute_plan(repaired, self.client, names, state)
        except Exception:
            logger.exception("Repaired plan execution failed conversation_id=%s", conversation_id)
            return plan, execution

        if repaired_execution.get("success"):
            return repaired, repaired_execution
        return plan, execution

    async def confirm(self, conversation_id, owner_id="anonymous"):
        self.store._owned(conversation_id, owner_id)
        state = self.states.get(conversation_id)
        draft = state.pending_draft_id if state else None
        if not draft:
            # Sunucu yeniden baslatilinca bellekteki durum kaybolur; kalici kayda bak.
            draft = self.store.pending_draft(conversation_id)
        if draft:
            return await self.chat(
                conversation_id,
                f"{draft} numaralı taslağı onayla ve siparişi oluştur",
                owner_id,
            )

        # Taslak yoksa bekleyen teslim alma olabilir; o da onay gerektiriyor.
        if state and state.pending_receive_ids:
            return await self.chat(
                conversation_id,
                "Bekleyen teslimatları onaylıyorum, stoğa al",
                owner_id,
            )

        raise HTTPException(409, "Onay bekleyen işlem bulunamadı.")


def owner(x_client_id):
    if not x_client_id or len(x_client_id) > 100:
        raise HTTPException(401, "İstemci kimliği gerekli.")
    return x_client_id


@asynccontextmanager
async def lifespan(app):
    client = MCPClient({"stock-server": STOCK_SERVER_PATH, "marketplace-server": MARKETPLACE_SERVER_PATH})
    await client.connect()
    app.state.agent = AgentApplication(client, LLMService())
    yield
    app.state.agent.store.close()
    await client.close()


app = FastAPI(title="Smart Stock LLM Host API", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("LLM_CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Client-Id"])


@app.get("/api/health")
async def health(): return {"status": "ok"}


@app.get("/api/conversations")
async def list_conversations(limit: int = 50, offset: int = 0, x_client_id: str | None = Header(None)):
    return {"items": app.state.agent.store.list(owner(x_client_id), min(max(limit, 1), 100), max(offset, 0))}


@app.post("/api/conversations", status_code=201)
async def create_conversation(body: CreateConversationRequest, x_client_id: str | None = Header(None)):
    return app.state.agent.store.create(owner(x_client_id), body.title)


@app.post("/api/chat")
async def chat(body: ChatRequest, x_client_id: str | None = Header(None)):
    request_id = str(uuid.uuid4())
    try:
        return await app.state.agent.chat(body.conversationId, body.message, owner(x_client_id))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat request failed request_id=%s conversation_id=%s", request_id, body.conversationId)
        raise HTTPException(500, f"İşlem beklenmeyen bir hatayla tamamlanamadı. Takip kodu: {request_id}") from exc


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: str, x_client_id: str | None = Header(None)):
    return app.state.agent.store.get(conversation_id, owner(x_client_id))


@app.post("/api/conversations/{conversation_id}/confirm")
async def confirm(conversation_id: str, x_client_id: str | None = Header(None)):
    try:
        return await app.state.agent.confirm(conversation_id, owner(x_client_id))
    except HTTPException:
        raise
    except Exception as exc:
        request_id = str(uuid.uuid4())
        logger.exception("Confirmation failed request_id=%s conversation_id=%s", request_id, conversation_id)
        raise HTTPException(500, f"Onay işlemi tamamlanamadı. Takip kodu: {request_id}") from exc


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def clear(conversation_id: str, x_client_id: str | None = Header(None)):
    app.state.agent.store.delete(conversation_id, owner(x_client_id))
    return Response(status_code=204)