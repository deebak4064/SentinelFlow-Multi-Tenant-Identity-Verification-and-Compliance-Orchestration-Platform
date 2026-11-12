import contextvars
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent
from app.security.permissions import Principal

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
client_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("client_ip", default=None)


def record(
    db: AsyncSession,
    action: str,
    *,
    org_id: uuid.UUID | None,
    principal: Principal | None = None,
    resource_type: str | None = None,
    resource_id=None,
    details: dict | None = None,
    actor_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
) -> None:
    """Stage an audit event in the caller's transaction so it commits atomically with the change."""
    db.add(
        AuditEvent(
            org_id=org_id,
            actor_type=principal.kind if principal else (actor_type or "system"),
            actor_id=principal.actor_id if principal else actor_id,
            actor_label=principal.label if principal else actor_label,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip=client_ip_var.get(),
            request_id=request_id_var.get(),
            details=details or {},
        )
    )
