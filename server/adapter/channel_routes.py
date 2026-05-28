from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.wechat_channel_manager import WechatChannelManagerError


class CreateAccountRequest(BaseModel):
    id: str
    name: str = ""
    default_agent_id: str = ""
    auto_start: bool = False
    proxy: str = ""


def create_channel_router(container: AppContainer) -> APIRouter:
    def require_channel_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/channels",
        tags=["channels"],
        dependencies=[Depends(require_channel_auth)],
    )

    def manager():
        if container.wechat_channel_manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="wechat channel service is unavailable",
            )
        return container.wechat_channel_manager

    # ------------------------------------------------------------------
    # multi-account endpoints
    # ------------------------------------------------------------------

    @router.get("/wechat/accounts")
    async def list_accounts():
        return {"accounts": await manager().all_statuses()}

    @router.post("/wechat/accounts")
    async def create_account(body: CreateAccountRequest):
        try:
            result = await manager().add_account(body.model_dump())
            return {"ok": True, "account": result}
        except WechatChannelManagerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete("/wechat/accounts/{account_id}")
    async def delete_account(account_id: str):
        try:
            await manager().remove_account(account_id)
            return {"ok": True}
        except WechatChannelManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get("/wechat/accounts/{account_id}/status")
    async def get_account_status(account_id: str):
        try:
            return {"account": await manager().account_status(account_id)}
        except WechatChannelManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/wechat/accounts/{account_id}/start")
    async def start_account(account_id: str):
        try:
            return {"account": await manager().start_account(account_id)}
        except WechatChannelManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/wechat/accounts/{account_id}/stop")
    async def stop_account(account_id: str):
        try:
            return {"account": await manager().stop_account(account_id)}
        except WechatChannelManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # legacy single-account endpoints (delegate to first account)
    # ------------------------------------------------------------------

    @router.get("/wechat/status")
    async def wechat_status():
        first_id = manager().first_account_id()
        if first_id is None:
            return {"wechat": None}
        account = await manager().account_status(first_id)
        return {"wechat": account["status"]}

    @router.post("/wechat/start")
    async def wechat_start():
        first_id = manager().first_account_id()
        if first_id is None:
            raise HTTPException(status_code=400, detail="没有可用的微信账号")
        account = await manager().start_account(first_id)
        return {"wechat": account["status"]}

    @router.post("/wechat/stop")
    async def wechat_stop():
        first_id = manager().first_account_id()
        if first_id is None:
            raise HTTPException(status_code=400, detail="没有可用的微信账号")
        account = await manager().stop_account(first_id)
        return {"wechat": account["status"]}

    return router
