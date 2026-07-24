# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from crete_plane_ai.auth import InternalBodyLimitMiddleware, require_internal_auth
from crete_plane_ai.azure import AzureError, AzureOpenAIClient, AzureStreamEvent
from crete_plane_ai.config import Settings, get_settings
from crete_plane_ai.database import Repository
from crete_plane_ai.schemas import (
    ActionCompleteRequest,
    ChatRequest,
    CreateCommentArguments,
    CreateSubtaskArguments,
    EditIssueDescriptionArguments,
    IndexDeleteRequest,
    IndexRequest,
    RetrieveRequest,
    ThreadCreate,
)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


def _bounded_history(history: list[dict[str, Any]], max_chars: int) -> list[dict[str, str]]:
    bounded: list[dict[str, str]] = []
    remaining = max_chars
    for message in reversed(history):
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        bounded.insert(0, {"role": role, "content": content[:remaining]})
        remaining -= min(len(content), remaining)
        if remaining == 0:
            break
    return bounded


def _validated_action(event: AzureStreamEvent, body: ChatRequest) -> tuple[str, dict[str, Any]]:
    if event.action_name is None or event.arguments is None:
        raise AzureError("chat tool proposal was incomplete")
    allowed_issue_pairs = {(item.project_id, item.object_id) for item in body.context_items}
    if body.project_id is not None and body.issue_id is not None:
        allowed_issue_pairs.add((body.project_id, body.issue_id))
    try:
        if event.action_name == "create_comment":
            arguments = CreateCommentArguments.model_validate(event.arguments)
            issue_pair = (arguments.project_id, arguments.issue_id)
        elif event.action_name == "edit_issue_description":
            arguments = EditIssueDescriptionArguments.model_validate(event.arguments)
            issue_pair = (arguments.project_id, arguments.issue_id)
        elif event.action_name == "create_subtask":
            arguments = CreateSubtaskArguments.model_validate(event.arguments)
            issue_pair = (arguments.project_id, arguments.parent_issue_id)
        else:
            raise AzureError("chat proposed an unsupported action")
    except ValidationError as error:
        raise AzureError("chat tool proposal failed validation") from error
    if issue_pair not in allowed_issue_pairs:
        raise AzureError("chat tool proposal referenced an unavailable object")
    return event.action_name, arguments.model_dump(mode="json")


def create_app(
    *,
    settings: Settings | None = None,
    repository: Repository | None = None,
    azure_client: AzureOpenAIClient | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_settings = settings or get_settings()
        active_repository = repository or Repository(active_settings)
        active_azure = azure_client or AzureOpenAIClient(active_settings)
        application.state.settings = active_settings
        application.state.repository = active_repository
        application.state.azure = active_azure
        try:
            await active_repository.open()
        except Exception:
            try:
                await active_azure.close()
            finally:
                await active_repository.close()
            raise
        try:
            yield
        finally:
            try:
                await active_azure.close()
            finally:
                await active_repository.close()

    application = FastAPI(
        title="Crete Plane AI",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(InternalBodyLimitMiddleware)
    if settings is not None:
        application.state.settings = settings
    if repository is not None:
        application.state.repository = repository
    if azure_client is not None:
        application.state.azure = azure_client

    @application.get("/healthz")
    async def health(request: Request) -> dict[str, str]:
        try:
            await request.app.state.repository.healthcheck()
        except Exception as error:
            raise HTTPException(status_code=503, detail="database is unavailable") from error
        return {"status": "ok"}

    router = APIRouter(
        prefix="/internal",
        dependencies=[Depends(require_internal_auth)],
    )

    @router.get("/workspaces/{workspace_id}/threads")
    async def list_threads(
        request: Request,
        workspace_id: UUID,
        user_id: Annotated[UUID, Query()],
    ) -> dict[str, Any]:
        threads = await request.app.state.repository.list_threads(workspace_id, user_id)
        return {"threads": threads}

    @router.post("/workspaces/{workspace_id}/threads", status_code=status.HTTP_201_CREATED)
    async def create_thread(
        request: Request,
        workspace_id: UUID,
        body: ThreadCreate,
    ) -> dict[str, Any]:
        return await request.app.state.repository.create_thread(
            workspace_id=workspace_id,
            **body.model_dump(),
        )

    @router.get("/workspaces/{workspace_id}/threads/{thread_id}")
    async def get_thread(
        request: Request,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: Annotated[UUID, Query()],
    ) -> dict[str, Any]:
        thread = await request.app.state.repository.get_thread(workspace_id, thread_id, user_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return thread

    @router.delete(
        "/workspaces/{workspace_id}/threads/{thread_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_thread(
        request: Request,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: Annotated[UUID, Query()],
    ) -> Response:
        deleted = await request.app.state.repository.delete_thread(workspace_id, thread_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="thread not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/retrieve")
    async def retrieve(request: Request, body: RetrieveRequest) -> dict[str, Any]:
        try:
            embedding = await request.app.state.azure.embed(body.query)
        except AzureError as error:
            raise HTTPException(status_code=502, detail="AI embedding provider unavailable") from error
        matches = await request.app.state.repository.retrieve(
            workspace_id=body.workspace_id,
            allowed_project_ids=body.allowed_project_ids,
            project_id=body.project_id,
            embedding=embedding,
            limit=body.limit,
        )
        return {"matches": [{"object_id": match["object_id"], "score": float(match["score"])} for match in matches]}

    @router.post("/index")
    async def index_document(request: Request, body: IndexRequest) -> dict[str, Any]:
        embedding_input = f"{body.title}\n\n{body.content}"[: request.app.state.settings.ai_max_embedding_input_chars]
        try:
            embedding = await request.app.state.azure.embed(embedding_input)
        except AzureError as error:
            raise HTTPException(status_code=502, detail="AI embedding provider unavailable") from error
        indexed = await request.app.state.repository.upsert_document(
            **body.model_dump(),
            embedding=embedding,
        )
        return {"object_id": body.object_id, "indexed": indexed}

    @router.post("/index/delete")
    async def delete_document(request: Request, body: IndexDeleteRequest) -> dict[str, Any]:
        deleted = await request.app.state.repository.delete_document(
            body.object_type,
            body.object_id,
            body.workspace_id,
        )
        return {"object_id": body.object_id, "deleted": deleted}

    @router.post("/chat")
    async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
        repository = request.app.state.repository
        active_settings = request.app.state.settings
        if len(body.context_items) > active_settings.ai_max_context_items:
            raise HTTPException(status_code=422, detail="too many context items")
        context_chars = sum(len(item.title) + len(item.content) for item in body.context_items)
        if context_chars > active_settings.ai_max_context_chars:
            raise HTTPException(status_code=422, detail="context items exceed the configured size limit")
        if not await repository.thread_exists(body.workspace_id, body.thread_id, body.user_id):
            raise HTTPException(status_code=404, detail="thread not found")
        history = _bounded_history(
            await repository.get_history(
                body.thread_id,
                active_settings.ai_max_history_messages,
            ),
            active_settings.ai_max_history_chars,
        )
        await repository.add_message(body.thread_id, "user", body.prompt)

        async def event_stream():
            assistant_text: list[str] = []
            proposed_actions: list[tuple[str, dict[str, Any]]] = []
            try:
                async for event in request.app.state.azure.stream_chat(
                    deployment=body.model,
                    history=history,
                    prompt=body.prompt,
                    context_items=body.context_items,
                ):
                    if event.kind == "delta" and event.delta is not None:
                        assistant_text.append(event.delta)
                        yield _sse("message.delta", {"delta": event.delta})
                    elif event.kind == "tool_call":
                        action_name, arguments = _validated_action(event, body)
                        proposed_actions.append((action_name, arguments))
                message = await repository.add_message(
                    body.thread_id,
                    "assistant",
                    "".join(assistant_text),
                )
                for action_name, arguments in proposed_actions:
                    proposal = await repository.create_action(
                        thread_id=body.thread_id,
                        message_id=message["id"],
                        workspace_id=body.workspace_id,
                        user_id=body.user_id,
                        action_name=action_name,
                        arguments=arguments,
                    )
                    yield _sse("proposal.created", proposal)
                yield _sse("message.completed", message)
            except asyncio.CancelledError:
                raise
            except AzureError:
                yield _sse("error", {"message": "AI provider response failed"})
            except Exception:
                yield _sse("error", {"message": "AI response could not be completed"})
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/actions/{action_id}")
    async def get_action(
        request: Request,
        action_id: UUID,
        workspace_id: Annotated[UUID, Query()],
        user_id: Annotated[UUID, Query()],
    ) -> dict[str, Any]:
        action = await request.app.state.repository.get_pending_action(
            action_id,
            workspace_id,
            user_id,
        )
        if action is None:
            raise HTTPException(status_code=404, detail="pending action not found")
        return action

    @router.post("/actions/{action_id}/complete")
    async def complete_action(
        request: Request,
        action_id: UUID,
        body: ActionCompleteRequest,
    ) -> dict[str, Any]:
        action = await request.app.state.repository.complete_action(
            action_id,
            body.workspace_id,
            body.user_id,
            body.result,
        )
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        return action

    application.include_router(router)
    return application


app = create_app()
