"""HTTP transport for the Smart Stock agent.

The API deliberately reuses app.py's plan parser, executor, state and formatters so
CLI and web requests have one set of execution/security rules.
"""
import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from app import (ConversationState, execute_plan, format_final_answer,
                 format_procurement_plan, format_purchase_draft,
                 get_execution_plan_prompt, is_plan_valid, parse_execution_plan)
from llm import LLMService
from mcp_client import MCPClient
from app import STOCK_SERVER_PATH, MARKETPLACE_SERVER_PATH

WRITE_TOOLS={"create_purchase_draft","place_order","create_incoming_order","receive_order"}
conversations:dict[str,dict]={}

class ChatRequest(BaseModel):
    conversationId:str=Field(min_length=1,max_length=100)
    message:str=Field(min_length=1,max_length=4000)

def summary(value):
    text=json.dumps(value,ensure_ascii=False,default=str) if not isinstance(value,str) else value
    return text[:300]+("…" if len(text)>300 else "")

class AgentApplication:
    def __init__(self,client:MCPClient,llm:LLMService): self.client,self.llm=client,llm
    async def chat(self,conversation_id:str,message:str):
        state=conversations.setdefault(conversation_id,{"state":ConversationState(),"messages":[]})["state"]
        state.last_user_message=message
        write_intent=any(w in message.lower() for w in ["sipariş","taslak","satın al","oluştur","place","draft","order"])
        permission="FULL" if write_intent else "PLAN"
        tools=await self.client.list_tools(); names={t.name for t in tools}
        cached={k:v for k,v in {"last_cheapest_plan":state.last_cheapest_plan,"last_fastest_plan":state.last_fastest_plan}.items() if is_plan_valid(v)}
        prompt=get_execution_plan_prompt(tools,state.last_plan,state,cached)
        raw=self.llm.generate([{"role":"system","content":prompt},*state.history,{"role":"user","content":message}])
        plan=parse_execution_plan(raw)
        if permission=="PLAN" and any(s.get("tool") in WRITE_TOOLS for s in plan.get("steps",[])):
            raise HTTPException(403,"Salt okunur istekte yazma işlemi engellendi.")
        started=time.perf_counter(); execution=await execute_plan(plan,self.client,names,state)
        trace=[]
        for step in plan.get("steps",[]):
            sid=step.get("id","step") ; result=execution.get("results",{}).get(sid)
            failed=execution.get("failed_step")==sid
            trace.append({"stepId":sid,"tool":step.get("tool"),"arguments":step.get("arguments",{}),"status":"failed" if failed else "success","resultSummary":summary(execution.get("error") if failed else result),"durationMs":round((time.perf_counter()-started)*1000/ max(1,len(plan.get("steps",[]))))})
        final=execution.get("last_result",{})
        last_tool=plan.get("steps",[{}])[-1].get("tool") if plan.get("steps") else None
        answer=format_purchase_draft(final) if last_tool=="create_purchase_draft" else format_procurement_plan(final) if last_tool=="create_procurement_plan" else format_final_answer(final)
        if not execution.get("success"): answer=f"İşlem tamamlanamadı: {execution.get('error','Bilinmeyen hata')}"
        draft_id=next((r.get("draftId") or r.get("draft_id") or r.get("id") for r in execution.get("results",{}).values() if isinstance(r,dict) and (r.get("draftId") or r.get("draft_id"))),None)
        if draft_id: state.pending_draft_id=int(draft_id)
        response={"conversationId":conversation_id,"permissionLevel":permission,"plan":plan,"trace":trace,"finalAnswer":answer,"pendingDraftId":state.pending_draft_id}
        conversations[conversation_id]["messages"].append(response)
        return response
    async def confirm(self,conversation_id:str):
        entry=conversations.get(conversation_id); draft=entry and entry["state"].pending_draft_id
        if not draft: raise HTTPException(409,"Onay bekleyen taslak bulunamadı.")
        return await self.chat(conversation_id,f"{draft} numaralı taslağı onayla ve siparişi oluştur")

@asynccontextmanager
async def lifespan(app:FastAPI):
    client=MCPClient({"stock-server":STOCK_SERVER_PATH,"marketplace-server":MARKETPLACE_SERVER_PATH});await client.connect();app.state.agent=AgentApplication(client,LLMService());yield;await client.close()

app=FastAPI(title="Smart Stock LLM Host API",lifespan=lifespan)
origins=[x.strip() for x in os.getenv("LLM_CORS_ALLOWED_ORIGINS","http://localhost:5173").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["GET","POST","DELETE"],allow_headers=["Content-Type"])
@app.get('/api/health')
async def health(): return {"status":"ok"}
@app.post('/api/chat')
async def chat(body:ChatRequest): return await app.state.agent.chat(body.conversationId,body.message)
@app.get('/api/conversations/{conversation_id}')
async def conversation(conversation_id:str):
    if conversation_id not in conversations: raise HTTPException(404,"Konuşma bulunamadı.")
    return {"conversationId":conversation_id,"messages":conversations[conversation_id]["messages"]}
@app.post('/api/conversations/{conversation_id}/confirm')
async def confirm(conversation_id:str): return await app.state.agent.confirm(conversation_id)
@app.delete('/api/conversations/{conversation_id}',status_code=204)
async def clear(conversation_id:str): conversations.pop(conversation_id,None)
