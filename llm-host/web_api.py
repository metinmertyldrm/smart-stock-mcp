"""Persistent HTTP transport for the Smart Stock agent."""
import json
import logging
import os
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
                 format_final_answer, format_procurement_plan, format_purchase_draft,
                 build_repair_instruction, get_execution_plan_prompt, is_plan_valid,
                 parse_execution_plan,
                 resolve_step_arguments, validate_plan_against_state)
from llm import LLMService
from mcp_client import MCPClient
from prompt import get_reasoning_prompt

WRITE_TOOLS = {"create_purchase_draft", "place_order", "create_incoming_order",
               "create_incoming_orders", "receive_order"}
WRITE_INTENT_WORDS = ("sipariş", "taslak", "satın al", "oluştur", "place", "draft", "order")
CONFIRM_INTENT_WORDS = ("evet", "onay", "onayla", "onaylıyorum", "devam", "tamam", "yes", "confirm")
DB_PATH = os.getenv("LLM_CONVERSATIONS_DB", os.path.join(os.path.dirname(__file__), "conversations.db"))
# Gecmis promptun icine giriyor; num_ctx tasmasin diye hem adet hem uzunluk sinirli.
HISTORY_MESSAGES = int(os.getenv("LLM_HISTORY_MESSAGES", "8"))
HISTORY_CHARS = int(os.getenv("LLM_HISTORY_CHARS", "800"))
logger = logging.getLogger(__name__)


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
        row = self.db.execute("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row and row["owner_id"] != owner_id:
            raise HTTPException(404, "Sohbet bulunamadı.")
        if not row:
            title = first_message.strip()[:60] + ("…" if len(first_message.strip()) > 60 else "")
            self.create(owner_id, title or "Yeni sohbet", conversation_id)

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


class AgentApplication:
    def __init__(self, client, llm, store=None):
        self.client, self.llm = client, llm
        self.store = store or ConversationStore()
        self.states = {}

    async def chat(self, conversation_id, message, owner_id="anonymous"):
        message = message.strip()
        if not message:
            raise HTTPException(422, "Mesaj boş olamaz.")
        self.store.ensure(conversation_id, owner_id, message)
        state = self.states.setdefault(conversation_id, ConversationState())
        if state.pending_draft_id is None:
            state.pending_draft_id = self.store.pending_draft(conversation_id)
        state.history = self.store.history(conversation_id)
        state.last_user_message = message
        normalized = message.casefold()
        write_intent = any(word in normalized for word in WRITE_INTENT_WORDS)
        confirm_intent = state.pending_draft_id is not None and any(word in normalized for word in CONFIRM_INTENT_WORDS)
        permission = "FULL" if write_intent or confirm_intent else "PLAN"
        tools = await self.client.list_tools()
        names = {t.name for t in tools}
        cached = {k: v for k, v in {"last_cheapest_plan": state.last_cheapest_plan, "last_fastest_plan": state.last_fastest_plan}.items() if is_plan_valid(v)}
        system_prompt = get_execution_plan_prompt(tools, state.last_plan, state, cached)
        raw = self.llm.generate([{"role": "system", "content": system_prompt}, *state.history, {"role": "user", "content": message}])
        try:
            plan = parse_execution_plan(raw)
            validate_plan_against_state(plan, state)
        except ValueError as exc:
            # Dogrulama hatasi execute_plan'den ONCE olusur; onarim dongusu buraya da uygulanmali.
            plan = self.repair_invalid_plan(system_prompt, message, raw, exc, state, conversation_id)
            if plan is None:
                return self.clarification_response(conversation_id, message, permission, state, exc)
        if permission == "PLAN" and any(s.get("tool") in WRITE_TOOLS for s in plan.get("steps", [])):
            raise HTTPException(403, "Salt okunur istekte yazma işlemi engellendi.")
        self.store.add_message(conversation_id, "user", message)
        started = time.perf_counter()
        execution = await execute_plan(plan, self.client, names, state)
        if not execution.get("success"):
            plan, execution = await self.repair(system_prompt, message, plan, execution, names, state, permission, conversation_id)
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
            trace.append({"stepId": sid, "tool": step.get("tool"), "arguments": step.get("arguments", {}), "status": status, "resultSummary": summary(detail), "durationMs": round((time.perf_counter() - started) * 1000 / max(1, len(plan.get("steps", []))))})
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

        goal = plan.get("goal", "").upper()
        if execution.get("success") and goal == "REASON":
            reasoning_data = clean_tool_results_for_reasoning(execution.get("results", {}))
            answer = self.llm.generate([{"role": "system", "content": get_reasoning_prompt(message, reasoning_data)}]).strip()
        elif goal == "CHAT":
            answer = final.get("chat_answer", "")
        else:
            answer = format_purchase_draft(final) if last_tool == "create_purchase_draft" else format_procurement_plan(final) if last_tool == "create_procurement_plan" else format_final_answer(final)
        if not execution.get("success"):
            answer = "İşlem tamamlanamadı. Lütfen isteğinizi kontrol edip yeniden deneyin."
        draft_id = next((r.get("draftId") or r.get("draft_id") for r in execution.get("results", {}).values() if isinstance(r, dict) and (r.get("draftId") or r.get("draft_id"))), None)
        if draft_id:
            state.pending_draft_id = int(draft_id)
        if execution.get("success") and last_tool == "place_order":
            state.pending_draft_id = None
        response = {"conversationId": conversation_id, "permissionLevel": permission, "plan": plan, "trace": trace, "finalAnswer": answer, "pendingDraftId": state.pending_draft_id}
        self.store.add_message(conversation_id, "assistant", answer, "success" if execution.get("success") else "failed", response)
        return response

    def repair_invalid_plan(self, system_prompt, message, raw, error, state, conversation_id):
        """Plan dogrulamadan gecemediginde modele hatayi geri verip bir kez daha dener."""
        logger.info("Plan validation failed (%s); attempting repair conversation_id=%s", error, conversation_id)
        try:
            repaired_raw = self.llm.generate([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_repair_instruction(
                    message,
                    {"invalid_plan_output": raw},
                    {"success": False, "stage": "plan_validation", "error": str(error)},
                    "",
                )},
            ])
            repaired = parse_execution_plan(repaired_raw)
            validate_plan_against_state(repaired, state)
            return repaired
        except Exception:
            logger.exception("Invalid plan could not be repaired conversation_id=%s", conversation_id)
            return None

    def clarification_response(self, conversation_id, message, permission, state, reason):
        """Onarim da tutmadi: zinciri durdur, kullaniciya ne yapamadigimizi acikla ve sor."""
        answer = (
            "İsteğinizi çalıştırılabilir bir işlem planına dönüştüremedim. "
            f"Karşılaştığım sorun: {reason} "
            "İsteğinizi biraz daha belirgin yazar mısınız? Örneğin hangi ürün grubunu "
            "(stokta olmayanlar, kritik seviyedekiler, belirli bir kategori), hangi ölçütü "
            "(en ucuz, en hızlı, en yüksek puanlı) ve sonucu satın alma planı olarak mı yoksa "
            "taslak sipariş olarak mı istediğinizi belirtebilirsiniz."
        )
        self.store.add_message(conversation_id, "user", message)
        response = {
            "conversationId": conversation_id,
            "permissionLevel": permission,
            "plan": {"type": "execution_plan", "goal": "CLARIFY", "steps": []},
            "trace": [],
            "finalAnswer": answer,
            "pendingDraftId": state.pending_draft_id,
        }
        self.store.add_message(conversation_id, "assistant", answer, "failed", response)
        return response

    async def repair(self, system_prompt, message, plan, execution, names, state, permission, conversation_id):
        """Basarisiz plani bir kez LLM'e geri verip duzeltilmis planla yeniden dener."""
        logger.info("Plan failed at step=%s error=%s; attempting repair conversation_id=%s",
                    execution.get("failed_step"), execution.get("error"), conversation_id)
        try:
            repaired_raw = self.llm.generate([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_repair_instruction(message, plan, execution, plan.get("goal", "").upper())},
            ])
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
        if not draft:
            raise HTTPException(409, "Onay bekleyen taslak bulunamadı.")
        return await self.chat(conversation_id, f"{draft} numaralı taslağı onayla ve siparişi oluştur", owner_id)


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
