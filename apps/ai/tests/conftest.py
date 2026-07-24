# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from crete_plane_ai.auth import signature_for
from crete_plane_ai.config import Settings
from crete_plane_ai.main import create_app
from crete_plane_ai.schemas import ReportFilters, ReportPlan


class FakeRepository:
    def __init__(self) -> None:
        self.workspace_id = uuid4()
        self.user_id = uuid4()
        self.thread_id = uuid4()
        self.action_id = uuid4()
        self.messages: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.nonces: set[UUID] = set()
        self.retrieve_arguments: dict[str, Any] | None = None
        self.index_arguments: dict[str, Any] | None = None
        self.delete_arguments: dict[str, Any] | None = None

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> None:
        return None

    async def claim_request_nonce(self, nonce: UUID, _expires_at: int) -> bool:
        if nonce in self.nonces:
            return False
        self.nonces.add(nonce)
        return True

    async def list_threads(self, workspace_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        if workspace_id != self.workspace_id or user_id != self.user_id:
            return []
        return [{"id": self.thread_id, "workspace_id": workspace_id, "user_id": user_id}]

    async def create_thread(self, **values: Any) -> dict[str, Any]:
        return {"id": self.thread_id, **values}

    async def get_thread(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        if (workspace_id, thread_id, user_id) != (
            self.workspace_id,
            self.thread_id,
            self.user_id,
        ):
            return None
        return {
            "id": thread_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "messages": self.messages,
            "proposals": self.actions,
        }

    async def delete_thread(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> bool:
        return (workspace_id, thread_id, user_id) == (
            self.workspace_id,
            self.thread_id,
            self.user_id,
        )

    async def thread_exists(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> bool:
        return (workspace_id, thread_id, user_id) == (
            self.workspace_id,
            self.thread_id,
            self.user_id,
        )

    async def get_history(self, thread_id: UUID, limit: int) -> list[dict[str, Any]]:
        return self.messages[-limit:]

    async def add_message(
        self,
        thread_id: UUID,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        message = {"id": uuid4(), "role": role, "content": content, "citations": citations or []}
        self.messages.append(message)
        return message

    async def create_action(self, **values: Any) -> dict[str, Any]:
        action = {"id": self.action_id, "status": "pending", **values}
        self.actions.append(action)
        return action

    async def get_pending_action(self, action_id: UUID, workspace_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        if (action_id, workspace_id, user_id) != (
            self.action_id,
            self.workspace_id,
            self.user_id,
        ):
            return None
        return next((action for action in self.actions if action["status"] == "pending"), None)

    async def complete_action(
        self,
        action_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (action_id, workspace_id, user_id) != (
            self.action_id,
            self.workspace_id,
            self.user_id,
        ):
            return None
        if not self.actions:
            self.actions.append(
                {
                    "id": action_id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "status": "pending",
                }
            )
        self.actions[0]["status"] = "completed"
        self.actions[0].setdefault("result", result)
        return self.actions[0]

    async def retrieve(self, **values: Any) -> list[dict[str, Any]]:
        self.retrieve_arguments = values
        return [{"object_id": uuid4(), "score": 0.91, "ignored": "not returned"}]

    async def upsert_document(self, **values: Any) -> bool:
        self.index_arguments = values
        return True

    async def delete_document(self, object_type: str, object_id: UUID, workspace_id: UUID) -> bool:
        self.delete_arguments = {
            "object_type": object_type,
            "object_id": object_id,
            "workspace_id": workspace_id,
        }
        return True


class FakeAzure:
    def __init__(self) -> None:
        self.embedding = [0.0] * 1536
        self.report_request = None

    async def close(self) -> None:
        return None

    async def embed(self, text: str) -> list[float]:
        return self.embedding

    async def plan_report(self, body):
        self.report_request = body
        return ReportPlan(
            mode="chat",
            filters=ReportFilters(
                project_ids=[],
                state_groups=[],
                state_ids=[],
                priorities=[],
                cycle_ids=[],
                module_ids=[],
                label_ids=[],
                issue_type_ids=[],
                assignee_ids=[],
                current_cycle=False,
                due="any",
                name_contains=None,
                created_after=None,
                created_before=None,
                updated_after=None,
            ),
            group_by="none",
            include_items=True,
            sort="updated_desc",
            limit=20,
        )

    async def stream_chat(self, **values: Any):
        if False:
            yield values


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ai_database_url=SecretStr("postgresql://unused"),
        crete_ai_shared_secret=SecretStr("s" * 32),
        azure_openai_endpoint="https://example.openai.azure.com/openai/v1/",
        azure_openai_api_key=SecretStr("unused"),
    )


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def azure() -> FakeAzure:
    return FakeAzure()


@pytest.fixture
def client(settings: Settings, repository: FakeRepository, azure: FakeAzure):
    app = create_app(settings=settings, repository=repository, azure_client=azure)
    with TestClient(app) as test_client:
        yield test_client


def signed_headers(
    settings: Settings,
    method: str,
    url: str,
    body: bytes = b"",
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    request_timestamp = str(timestamp if timestamp is not None else int(time.time()))
    request_nonce = nonce or uuid4().hex
    parsed_url = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parsed_url.query, keep_blank_values=True)))
    target = f"{parsed_url.path}?{query}" if query else parsed_url.path
    return {
        "X-Crete-AI-Timestamp": request_timestamp,
        "X-Crete-AI-Nonce": request_nonce,
        "X-Crete-AI-Signature": signature_for(
            settings.crete_ai_shared_secret.get_secret_value(),
            request_timestamp,
            request_nonce,
            method,
            target,
            body,
        ),
    }


def signed_json(
    client: TestClient,
    settings: Settings,
    method: str,
    url: str,
    payload: dict[str, Any],
):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.request(
        method,
        url,
        content=body,
        headers={"Content-Type": "application/json", **signed_headers(settings, method, url, body)},
    )
