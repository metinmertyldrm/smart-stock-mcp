"""Conversation state models, cache validity, and CLI persistence helpers.

This module owns the in-memory state shape shared by the CLI and web agent,
plus the legacy JSON persistence used by the CLI runtime. It has no MCP,
FastAPI, or Ollama dependency.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
    pending_receive_ids: list[int] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)


STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_state.json")


def is_plan_valid(plan) -> bool:
    """Return whether a cached/replenishment plan is still usable."""
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
            if (datetime.now(timezone.utc) - saved_dt).total_seconds() > 600:
                return False
            result = plan.result
            if isinstance(result, dict) and result.get("success") is False:
                return False
            return True
        except Exception:
            return False
    return False


def serialize_plan(plan):
    """Convert CachedProcurementPlan to the legacy JSON-compatible shape."""
    if plan is None:
        return None
    if isinstance(plan, dict):
        return plan
    return asdict(plan)


def _deserialize_plan(payload):
    if not payload:
        return None
    if "objective" in payload and "saved_at" in payload:
        return CachedProcurementPlan(
            objective=payload.get("objective"),
            items=payload.get("items"),
            result=payload.get("result"),
            saved_at=payload.get("saved_at"),
        )
    return payload


def save_state(state: ConversationState, path: str | None = None) -> None:
    """Persist the legacy CLI conversation state.

    The serialized field set intentionally matches the pre-extraction runtime
    so this refactor does not change persistence semantics.
    """
    target = path or STATE_FILE
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
            "history": state.history,
        }
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"Hafıza kaydedilirken hata: {exc}")


def load_state(path: str | None = None) -> ConversationState:
    """Load the legacy CLI JSON state, returning an empty state on failure."""
    target = path or STATE_FILE
    state = ConversationState()
    if not os.path.exists(target):
        return state

    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        state.last_plan = data.get("last_plan")
        state.last_cheapest_plan = _deserialize_plan(data.get("last_cheapest_plan"))
        state.last_fastest_plan = _deserialize_plan(data.get("last_fastest_plan"))
        state.last_product = data.get("last_product")
        state.last_replenishment = data.get("last_replenishment")
        state.references = data.get("references", {})
        state.last_reference_id = data.get("last_reference_id")
        state.last_user_message = data.get("last_user_message")
        state.pending_draft_id = data.get("pending_draft_id")
        state.history = data.get("history", [])
    except Exception as exc:
        print(f"Hafıza yüklenirken hata: {exc}")
    return state
