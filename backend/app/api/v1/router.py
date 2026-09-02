from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("", summary="API version metadata")
async def api_version() -> dict[str, str]:
    return {"name": "NovaCart Support API", "version": "v1"}
