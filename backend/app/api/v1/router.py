from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.tickets import conversation_router as staff_conversations_router
from app.api.v1.tickets import router as tickets_router
from app.api.v1.webhooks import router as webhooks_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(conversations_router)
router.include_router(knowledge_router)
router.include_router(agent_router)
router.include_router(tickets_router)
router.include_router(staff_conversations_router)
router.include_router(webhooks_router)


@router.get("", summary="API version metadata")
async def api_version() -> dict[str, str]:
    return {"name": "NovaCart Support API", "version": "v1"}
