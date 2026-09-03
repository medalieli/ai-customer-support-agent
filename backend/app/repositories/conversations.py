from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Conversation, Message, MessageRole


class ConversationRepository:
    """No unrestricted get-by-id method: every query requires trusted scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_for_customer(
        self, organization_id: UUID, customer_id: UUID, locale: str, title: str | None
    ) -> Conversation:
        conversation = Conversation(
            organization_id=organization_id,
            customer_id=customer_id,
            locale=locale,
            title=title,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def list_for_customer(
        self, organization_id: UUID, customer_id: UUID, limit: int, offset: int
    ) -> list[Conversation]:
        result = await self.session.scalars(
            select(Conversation)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.customer_id == customer_id,
            )
            .order_by(Conversation.created_at.desc(), Conversation.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result)

    async def get_for_customer(
        self, organization_id: UUID, customer_id: UUID, conversation_id: UUID
    ) -> Conversation | None:
        result = await self.session.scalars(
            select(Conversation).where(
                Conversation.organization_id == organization_id,
                Conversation.customer_id == customer_id,
                Conversation.id == conversation_id,
            )
        )
        return result.one_or_none()

    async def get_for_staff(
        self, organization_id: UUID, conversation_id: UUID
    ) -> Conversation | None:
        result = await self.session.scalars(
            select(Conversation).where(
                Conversation.organization_id == organization_id,
                Conversation.id == conversation_id,
            )
        )
        return result.one_or_none()

    async def add_message(
        self,
        conversation: Conversation,
        role: MessageRole,
        content: str,
        locale: str,
        sender_customer_id: UUID | None = None,
        sender_staff_id: UUID | None = None,
        visible_to_customer: bool = True,
    ) -> Message:
        await self.session.scalar(
            select(Conversation)
            .where(
                Conversation.organization_id == conversation.organization_id,
                Conversation.id == conversation.id,
            )
            .with_for_update()
        )
        last_sequence = await self.session.scalar(
            select(func.coalesce(func.max(Message.sequence_number), 0)).where(
                Message.organization_id == conversation.organization_id,
                Message.conversation_id == conversation.id,
            )
        )
        message = Message(
            organization_id=conversation.organization_id,
            conversation_id=conversation.id,
            sequence_number=int(last_sequence or 0) + 1,
            role=role,
            content=content,
            locale=locale,
            sender_customer_id=sender_customer_id,
            sender_staff_id=sender_staff_id,
            visible_to_customer=visible_to_customer,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(
        self, conversation: Conversation, after_sequence: int, limit: int
    ) -> list[Message]:
        result = await self.session.scalars(
            select(Message)
            .where(
                Message.organization_id == conversation.organization_id,
                Message.conversation_id == conversation.id,
                Message.sequence_number > after_sequence,
            )
            .order_by(Message.sequence_number)
            .limit(limit)
        )
        return list(result)
