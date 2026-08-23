"""Request-scoped RBAC enforcement for MCP tool execution.

The authenticated role is server-owned and stored in a ContextVar for the
lifetime of one HTTP request. MCPClient filters the visible tool catalog and
performs final authorization immediately before dispatch, so an LLM plan cannot
grant itself extra rights.

Plan-level authorization is also available as a preflight check. Callers can
reject the whole plan before any read or write tool runs, while dispatch-time
authorization remains a second independent safety boundary.

A ``None`` role intentionally preserves unrestricted trusted/internal test and
legacy anonymous execution. Production local-identity requests always set an
explicit role in ``secure_api``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

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


@dataclass(frozen=True)
class PlanAuthorizationViolation:
    """One forbidden plan step found during preflight authorization."""

    role: str
    step_id: str
    tool_name: str

    @property
    def user_message(self) -> str:
        if self.role == ROLE_VIEWER:
            return (
                "VIEWER rolü salt okunurdur. Bu istek kalıcı veri değişikliği gerektiriyor; "
                "hiçbir araç çalıştırılmadı. OPERATOR veya daha yetkili bir kullanıcıdan "
                "sipariş taslağı hazırlamasını isteyin."
            )
        if self.role == ROLE_OPERATOR:
            return (
                "OPERATOR rolü satın alma taslağı hazırlayabilir ancak sipariş verme veya "
                "teslimatı stoğa alma işlemini onaylayamaz. Hiçbir araç çalıştırılmadı; "
                "bu adım için MANAGER veya ADMIN yetkisi gerekir."
            )
        return (
            f"{self.role} rolü '{self.tool_name}' işlemini çalıştıramaz. "
            "Yetkisiz plan execution başlamadan durduruldu."
        )

    @property
    def technical_message(self) -> str:
        return (
            f"RBAC preflight blocked step={self.step_id} tool={self.tool_name} role={self.role}"
        )


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


def tool_visible(tool_name: str, *, role: str | None = None) -> bool:
    """Return whether a tool may be advertised to the current request."""
    effective_role = current_role() if role is None else normalize_role(role)
    return tool_name not in WRITE_TOOLS or tool_name in allowed_write_tools(effective_role)


def plan_authorization_violation(
    plan: dict, *, role: str | None = None
) -> PlanAuthorizationViolation | None:
    """Return the first forbidden write step without executing any plan step."""
    effective_role = current_role() if role is None else normalize_role(role)
    if effective_role is None:
        return None

    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return None

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        tool_name = step.get("tool")
        if not isinstance(tool_name, str) or tool_name not in WRITE_TOOLS:
            continue
        if tool_visible(tool_name, role=effective_role):
            continue
        step_id = step.get("id") or f"step_{index + 1}"
        return PlanAuthorizationViolation(
            role=effective_role,
            step_id=str(step_id),
            tool_name=tool_name,
        )
    return None


def authorize_plan(plan: dict, *, role: str | None = None) -> None:
    """Raise before execution when any plan step exceeds the current role."""
    violation = plan_authorization_violation(plan, role=role)
    if violation is None:
        return
    raise AuthorizationError(violation.technical_message)


def authorize_tool(tool_name: str, *, role: str | None = None) -> None:
    effective_role = current_role() if role is None else normalize_role(role)
    if tool_visible(tool_name, role=effective_role):
        return
    label = effective_role or "TRUSTED_INTERNAL"
    raise AuthorizationError(
        f"{label} rolü '{tool_name}' yazma aracını çalıştıramaz."
    )
