from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.config_file_service import InvalidConfigFileError
from server.app.workspace_file_service import (
    InvalidWorkspacePathError,
    WorkspaceFileNotTextError,
    WorkspaceFileTooLargeError,
)


class WorkspacePathRequest(BaseModel):
    path: str = ""


class WorkspaceWriteRequest(BaseModel):
    path: str
    content: str


def create_workspace_router(container: AppContainer) -> APIRouter:
    def require_workspace_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/workspace",
        tags=["workspace"],
        dependencies=[Depends(require_workspace_auth)],
    )

    @router.post("/list")
    def list_entries(payload: WorkspacePathRequest) -> dict[str, object]:
        try:
            entries = container.workspace_file_service.list_entries(payload.path)
        except InvalidWorkspacePathError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="工作目录路径无效") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目录不存在") from exc
        except NotADirectoryError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="路径不是目录") from exc

        return {
            "path": payload.path.strip().strip("/"),
            "entries": [entry.__dict__ for entry in entries],
        }

    @router.post("/read")
    def read_file(payload: WorkspacePathRequest) -> dict[str, object]:
        try:
            file = container.workspace_file_service.read_text_file(payload.path)
        except InvalidWorkspacePathError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="工作目录路径无效") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="路径是目录") from exc
        except WorkspaceFileTooLargeError as exc:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件超过 1MB，前端不直接打开") from exc
        except WorkspaceFileNotTextError as exc:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="只支持 UTF-8 文本文件") from exc

        return file.__dict__

    @router.put("/write")
    def write_file(payload: WorkspaceWriteRequest) -> dict[str, object]:
        normalized_path = payload.path.strip().strip("/")
        if normalized_path == "config.yaml":
            try:
                container.config_file_service.write_config(payload.content)
            except InvalidConfigFileError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"配置无效：{exc}") from exc
            file = container.workspace_file_service.read_text_file(normalized_path)
            return {"ok": True, "message": "config.yaml 已校验并保存", "file": file.__dict__}

        try:
            file = container.workspace_file_service.write_text_file(payload.path, payload.content)
        except InvalidWorkspacePathError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="工作目录路径无效") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="路径是目录") from exc
        except WorkspaceFileTooLargeError as exc:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="内容超过 1MB") from exc
        except WorkspaceFileNotTextError as exc:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="只支持可编辑文本文件") from exc

        return {"ok": True, "message": "文件已保存", "file": file.__dict__}

    return router
