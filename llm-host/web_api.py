"""Persistent HTTP transport for the Smart Stock agent."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import (MARKETPLACE_SERVER_PATH, STOCK_SERVER_PATH, ConversationState,
                 execute_plan, format_final_answer, format_procurement_plan,
                 format_purchase_draft, get_execution_plan_prompt, is_plan_valid,
                 parse_execution_plan)
from llm import LLMService
from mcp_client import MCPClient

WRITE_TOOLS = {"create_purchase_draft", "place_order", "create_incoming_order", "receive_order"}
DB_PATH = os.getenv("LLM_CONVERSATIONS_DB", os.path.join(os.path.dirname(__file__), "conversations.db"))


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

    def history(self, conversation_id, limit=20):
        rows = self.db.execute("SELECT role,content FROM messages WHERE conversation_id=? AND status='success' ORDER BY created_at DESC LIMIT ?", (conversation_id, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

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
        state.history = self.store.history(conversation_id)
        state.last_user_message = message
        self.store.add_message(conversation_id, "user", message)
        write_intent = any(w in message.lower() for w in ["sipariş", "taslak", "satın al", "oluştur", "place", "draft", "order"])
        permission = "FULL" if write_intent else "PLAN"
        tools = await self.client.list_tools()
        names = {t.name for t in tools}
        cached = {k: v for k, v in {"last_cheapest_plan": state.last_cheapest_plan, "last_fastest_plan": state.last_fastest_plan}.items() if is_plan_valid(v)}
        raw = self.llm.generate([{"role": "system", "content": get_execution_plan_prompt(tools, state.last_plan, state, cached)}, *state.history, {"role": "user", "content": message}])
        plan = parse_execution_plan(raw)
        if permission == "PLAN" and any(s.get("tool") in WRITE_TOOLS for s in plan.get("steps", [])):
            raise HTTPException(403, "Salt okunur istekte yazma işlemi engellendi.")
        started = time.perf_counter()
        execution = await execute_plan(plan, self.client, names, state)
        trace = []
        for step in plan.get("steps", []):
            sid = step.get("id", "step")
            result = execution.get("results", {}).get(sid)
            failed = execution.get("failed_step") == sid
            trace.append({"stepId": sid, "tool": step.get("tool"), "arguments": step.get("arguments", {}), "status": "failed" if failed else "success", "resultSummary": summary(execution.get("error") if failed else result), "durationMs": round((time.perf_counter() - started) * 1000 / max(1, len(plan.get("steps", []))))})
        final = execution.get("last_result", {})
        last_tool = plan.get("steps", [{}])[-1].get("tool") if plan.get("steps") else None
        answer = format_purchase_draft(final) if last_tool == "create_purchase_draft" else format_procurement_plan(final) if last_tool == "create_procurement_plan" else format_final_answer(final)
        if not execution.get("success"):
            answer = "İşlem tamamlanamadı. Lütfen isteğinizi kontrol edip yeniden deneyin."
        draft_id = next((r.get("draftId") or r.get("draft_id") for r in execution.get("results", {}).values() if isinstance(r, dict) and (r.get("draftId") or r.get("draft_id"))), None)
        if draft_id:
            state.pending_draft_id = int(draft_id)
        response = {"conversationId": conversation_id, "permissionLevel": permission, "plan": plan, "trace": trace, "finalAnswer": answer, "pendingDraftId": state.pending_draft_id}
        self.store.add_message(conversation_id, "assistant", answer, "success" if execution.get("success") else "failed", response)
        return response

    async def confirm(self, conversation_id, owner_id="anonymous"):
        self.store._owned(conversation_id, owner_id)
        state = self.states.get(conversation_id)
        draft = state and state.pending_draft_id
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
    try:
        return await app.state.agent.chat(body.conversationId, body.message, owner(x_client_id))
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[CHAT ERROR] {type(exc).__name__}: {exc}")
        raise HTTPException(503, "AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.") from exc


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: str, x_client_id: str | None = Header(None)):
    return app.state.agent.store.get(conversation_id, owner(x_client_id))


@app.post("/api/conversations/{conversation_id}/confirm")
async def confirm(conversation_id: str, x_client_id: str | None = Header(None)):
    return await app.state.agent.confirm(conversation_id, owner(x_client_id))


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def clear(conversation_id: str, x_client_id: str | None = Header(None)):
    app.state.agent.store.delete(conversation_id, owner(x_client_id))
    return Response(status_code=204)
