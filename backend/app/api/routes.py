from __future__ import annotations

import asyncio
import json
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from ..career import ResumeNotFound, ResumeParseError, ResumeStore, ResumeStoreError
from ..career.models import ResumeUploadResponse
from ..config import BASE_DIR
from ..contracts import TaskSnapshot, TaskStatus, UserRequest
from ..errors import PipelineStageError
from ..fake_executor import default_fake_executor
from ..models import (
    ClarificationRequest,
    LoginRequest,
    QueryRequest,
    QueryResult,
    SaveFieldMemoryRequest,
    SaveMemoryRequest,
    SchemaTable,
)
from ..security import AuthService, AuthUser
from ..services.askdata_service import AskDataService


router = APIRouter(prefix="/api")
service = AskDataService()
auth_service = AuthService()
fake_executor = default_fake_executor(BASE_DIR)
resume_store = ResumeStore()


def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthUser:
    if authorization is None:
        return AuthUser("local_user", "local", "本地用户", "local")
    user = auth_service.authenticate(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以执行该操作")
    return user


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ResumeParseError("简历文件大小限制必须为正数")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ResumeParseError(f"简历文件超过 {max_bytes} 字节限制")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", **service.status()}


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    result = auth_service.login(payload.username, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token, user = result
    return {"access_token": token, "token_type": "bearer", "user": user.public()}


@router.get("/auth/me")
def me(user: AuthUser = Depends(require_user)) -> dict[str, str]:
    return user.public()


@router.post("/auth/logout")
def logout(
    authorization: str | None = Header(default=None, alias="Authorization"),
    _: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    auth_service.logout(authorization)
    return {"logged_out": True}


@router.get("/schema", response_model=list[SchemaTable])
def schema(
    user: AuthUser = Depends(require_user),
) -> list[dict]:
    return service.visible_schema(user.user_id)


@router.get("/schema/search")
def search_schema(
    q: str,
    threshold: float | None = None,
    user: AuthUser = Depends(require_user),
) -> dict:
    if not q.strip():
        raise HTTPException(status_code=400, detail="检索问题不能为空")
    if threshold is not None and not 0 <= threshold <= 1:
        raise HTTPException(status_code=400, detail="阈值必须在0到1之间")
    try:
        return service.search_schema(q.strip(), threshold, user.user_id)
    except PipelineStageError as error:
        raise HTTPException(
            status_code=502,
            detail=f"{error.stage}失败：{error.message}",
        ) from error


@router.post("/schema/index/rebuild")
def rebuild_schema_index(_: AuthUser = Depends(require_admin)) -> dict:
    try:
        return service.rebuild_schema_index()
    except PipelineStageError as error:
        raise HTTPException(
            status_code=502,
            detail=f"{error.stage}失败：{error.message}",
        ) from error


@router.get("/config")
def config(_: AuthUser = Depends(require_user)) -> dict:
    return service.status()


@router.get("/mcp/tools")
def mcp_tools(
    user: AuthUser = Depends(require_user),
) -> list[dict]:
    """查看模型可用的MCP工具及其输入输出Schema。"""
    return service.list_mcp_tools(user.user_id)


@router.get("/skills")
def skills(_: AuthUser = Depends(require_user)) -> list[dict]:
    """查看当前启用的应用级Skill。"""
    return service.list_skills()


@router.post("/resumes", response_model=ResumeUploadResponse, status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_user),
) -> ResumeUploadResponse:
    try:
        content = await _read_upload(file, resume_store.limits.max_bytes)
        return resume_store.save(file.filename or "", content, user_id=user.user_id)
    except ResumeParseError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ResumeStoreError as error:
        raise HTTPException(status_code=500, detail="简历保存失败，请稍后重试") from error
    finally:
        await file.close()


@router.delete("/resumes/{resume_id}")
def delete_resume(
    resume_id: str,
    user: AuthUser = Depends(require_user),
) -> dict[str, object]:
    try:
        resume_store.delete(resume_id, user_id=user.user_id)
    except ResumeNotFound as error:
        raise HTTPException(status_code=404, detail="简历不存在") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="无权删除该简历") from error
    except ResumeStoreError as error:
        raise HTTPException(status_code=500, detail="简历删除失败，请稍后重试") from error
    return {"deleted": True, "resume_id": resume_id}


@router.post("/query", response_model=QueryResult)
def query(
    payload: QueryRequest,
    user: AuthUser = Depends(require_user),
) -> QueryResult:
    workspace = payload.workspace.model_dump(exclude_none=True) if payload.workspace else None
    return service.submit(payload.query, payload.session_id, workspace, user.user_id)


@router.post("/tasks", response_model=TaskSnapshot, status_code=202)
async def create_task(payload: UserRequest) -> TaskSnapshot:
    """创建阶段 0 假任务，不调用同步 AskData 或外部服务。"""

    return await fake_executor.create(payload)


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """通过 SSE 重放并持续推送任务事件，支持 ``Last-Event-ID`` 重连。"""

    snapshot = fake_executor.get(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    after_sequence = 0
    if last_event_id:
        if last_event_id.isdigit():
            after_sequence = int(last_event_id)
        else:
            after_sequence = fake_executor.sequence_after_event_id(task_id, last_event_id) or 0

    async def stream():
        nonlocal after_sequence
        sent_event_ids: set[str] = set()
        while True:
            if await request.is_disconnected():
                return
            events = fake_executor.events(task_id, after_sequence=after_sequence)
            for event in events:
                if event.event_id in sent_event_ids:
                    continue
                sent_event_ids.add(event.event_id)
                after_sequence = event.sequence
                yield {
                    "id": event.event_id,
                    "event": event.type,
                    "data": json.dumps(
                        event.model_dump(by_alias=True, mode="json"),
                        ensure_ascii=False,
                    ),
                }

            current = fake_executor.get(task_id)
            if current is None:
                return
            if current.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            } and after_sequence >= current.sequence:
                return
            await asyncio.sleep(0.05)

    return EventSourceResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        ping=10,
    )


@router.post("/tasks/{task_id}/clarify", response_model=QueryResult)
def clarify(
    task_id: str,
    payload: ClarificationRequest,
    user: AuthUser = Depends(require_user),
) -> QueryResult:
    try:
        return service.clarify(task_id, payload.option_id, user_id=user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="查询任务不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail="无效的澄清选项") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.get("/tasks/{task_id}")
def task(
    task_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> TaskSnapshot | QueryResult:
    # 阶段 0 假任务不依赖登录，便于直接验证协议；旧版 AskData
    # 查询任务仍然沿用原有的用户隔离和鉴权规则。
    fake_snapshot = fake_executor.get(task_id)
    if fake_snapshot is not None:
        return fake_snapshot
    user = require_user(authorization)
    try:
        return service.get_task(task_id, user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="查询任务不存在") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post("/memories")
def save_memory(
    payload: SaveMemoryRequest,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    try:
        service.save_memory(payload.task_id, user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="没有可保存的查询结果") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return {"saved": True}


@router.get("/memories")
def memories(user: AuthUser = Depends(require_user)) -> list[dict]:
    return service.list_memories(user.user_id)


@router.post("/memories/fields")
def save_field_memory(
    payload: SaveFieldMemoryRequest,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    try:
        service.save_field_memory(
            payload.table_id,
            payload.name,
            payload.label,
            payload.field_type,
            user.user_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"saved": True}


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    service.delete_memory(memory_id, user.user_id)
    return {"deleted": True}
