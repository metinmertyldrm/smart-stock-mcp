"""Request-scoped RBAC enforcement for MCP tool execution.

The authenticated role is server-owned and stored in a ContextVar for the
lifetime of one HTTP request. MCPClient performs the final tool authorization
immediately before dispatch, so an LLM plan cannot grant itself extra rights.

A ``None`` role intentionally preserves unrestricted trusted/internal test and
legacy anonymous execution. Production local-identity requests always set an
explicit role in ``secure_api``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

from identity import ROLE_ADMIN, ROLE_MANAGER, ROLE_OPERATOR, ROLE_VIEWER, normalize_role


DRAFT_WRITE_TOOLS = frozenset({"create_purchase_draft"})
CONFIRM_WRITE_TOOLS = frozenset(
    {
        "place_order",
        "create_incoming_order",
        "create_incoming_orders",
        "receive_order",
        "receive_orders",
    }
)
WRITE_TOOLS = DRAFT_WRITE_TOOLS | CONFIRM_WRITE_TOOLS

_current_role: ContextVar[str | None] = ContextVar("smart_stock_authenticated_role", default=None)


class AuthorizationError(PermissionError):
    """Raised when the authenticated role cannot dispatch an MCP tool."""


def set_current_role(role: str | None) -> Token:
    normalized = normalize_role(role) if role is not None else None
    return _current_role.set(normalized)


def reset_current_role(token: Token) -> None:
    _current_role.reset(token)


def current_role() -> str | None:
    return _current_role.get()


def allowed_write_tools(role: str | None) -> frozenset[str]:
    if role is None:
        return WRITE_TOOLS
    normalized = normalize_role(role)
    if normalized == ROLE_VIEWER:
        return frozenset()
    if normalized == ROLE_OPERATOR:
        return DRAFT_WRITE_TOOLS
    if normalized in {ROLE_MANAGER, ROLE_ADMIN}:
        return WRITE_TOOLS
    return frozenset()


def authorize_tool(tool_name: str, *, role: str | None = None) -> None:
    effective_role = current_role() if role is None else normalize_role(role)
    if tool_name not in WRITE_TOOLS:
        return
    if tool_name in allowed_write_tools(effective_role):
        return
    label = effective_role or "TRUSTED_INTERNAL"
    raise AuthorizationError(
        f"{label} rolü '{tool_name}' yazma aracını çalıştıramaz."
    )
