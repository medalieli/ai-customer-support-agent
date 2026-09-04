from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import AuditEvent

ALLOWED_METADATA_KEYS = {"method", "role", "path", "page_size", "provider", "topic", "status"}


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        organization_id: UUID,
        actor_type: str,
        action: str,
        outcome: str,
        *,
        actor_id: UUID | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        reason_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        safe_metadata = {
            key: value for key, value in (metadata or {}).items() if key in ALLOWED_METADATA_KEYS
        }
        event = AuditEvent(
            organization_id=organization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            reason_code=reason_code,
            metadata_json=safe_metadata,
        )
        self.session.add(event)
        await self.session.flush()
        return event
