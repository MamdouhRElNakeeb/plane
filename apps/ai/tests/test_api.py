# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import UTC, datetime
from uuid import uuid4

from conftest import FakeAzure, FakeRepository, signed_headers, signed_json

from crete_plane_ai.azure import AzureStreamEvent


def test_health_is_public(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_thread_crud_is_scoped_to_owner(client, settings, repository: FakeRepository) -> None:
    create_response = signed_json(
        client,
        settings,
        "POST",
        f"/internal/workspaces/{repository.workspace_id}/threads",
        {
            "user_id": str(repository.user_id),
            "authorization_version": 1,
            "title": "Release planning",
            "context_type": "workspace",
        },
    )
    assert create_response.status_code == 201
    assert create_response.json()["id"] == str(repository.thread_id)

    get_url = (
        f"/internal/workspaces/{repository.workspace_id}/threads/{repository.thread_id}?user_id={repository.user_id}"
    )
    assert client.get(get_url, headers=signed_headers(settings, "GET", get_url)).status_code == 200

    other_user_url = f"/internal/workspaces/{repository.workspace_id}/threads/{repository.thread_id}?user_id={uuid4()}"
    assert client.get(other_user_url, headers=signed_headers(settings, "GET", other_user_url)).status_code == 404

    delete_response = client.delete(get_url, headers=signed_headers(settings, "DELETE", get_url))
    assert delete_response.status_code == 204


def test_retrieve_returns_ranked_ids_only(
    client,
    settings,
    repository: FakeRepository,
) -> None:
    allowed_project = uuid4()
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/retrieve",
        {
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "query": "authentication regression",
            "allowed_project_ids": [str(allowed_project)],
            "project_id": str(allowed_project),
            "limit": 5,
        },
    )

    assert response.status_code == 200
    match = response.json()["matches"][0]
    assert set(match) == {"object_id", "score"}
    assert repository.retrieve_arguments is not None
    assert repository.retrieve_arguments["workspace_id"] == repository.workspace_id
    assert repository.retrieve_arguments["allowed_project_ids"] == [allowed_project]


def test_report_planner_is_signed_and_does_not_access_plane_repository(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
) -> None:
    project_id = uuid4()
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/report-plan",
        {
            "prompt": "Count unresolved work by status.",
            "context_type": "workspace",
            "current_date": "2026-07-24",
            "catalog": [
                {
                    "id": str(project_id),
                    "kind": "project",
                    "name": "Engineering",
                    "identifier": "ENG",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "chat"
    assert azure.report_request.prompt == "Count unresolved work by status."
    assert azure.report_request.catalog[0].id == project_id
    assert repository.retrieve_arguments is None


def test_index_generates_embedding_and_upserts(
    client,
    settings,
    repository: FakeRepository,
) -> None:
    object_id = uuid4()
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/index",
        {
            "object_type": "issue",
            "object_id": str(object_id),
            "workspace_id": str(repository.workspace_id),
            "project_id": str(uuid4()),
            "title": "Title",
            "content": "Current content",
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"object_id": str(object_id), "indexed": True}
    assert repository.index_arguments is not None
    assert len(repository.index_arguments["embedding"]) == 1536


def test_index_deletion_is_scoped_to_workspace_and_object_type(
    client,
    settings,
    repository: FakeRepository,
) -> None:
    object_id = uuid4()
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/index/delete",
        {
            "object_type": "issue",
            "object_id": str(object_id),
            "workspace_id": str(repository.workspace_id),
        },
    )

    assert response.status_code == 200
    assert repository.delete_arguments == {
        "object_type": "issue",
        "object_id": object_id,
        "workspace_id": repository.workspace_id,
    }


def test_chat_stream_persists_messages_and_proposes_without_executing(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    issue_id = uuid4()

    async def stream_chat(**_values):
        yield AzureStreamEvent(kind="delta", delta="I can draft that 【1】. Recheck 【1】, not 【99】. items[1].")
        yield AzureStreamEvent(
            kind="tool_call",
            action_name="create_comment",
            arguments={
                "project_id": str(project_id),
                "issue_id": str(issue_id),
                "comment_html": "<p>Ready for review.</p>",
            },
        )
        yield AzureStreamEvent(kind="completed")

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Add a ready-for-review comment.",
            "context_type": "work_item",
            "project_id": str(project_id),
            "issue_id": str(issue_id),
            "context_items": [
                {
                    "object_type": "issue",
                    "object_id": str(issue_id),
                    "project_id": str(project_id),
                    "title": "Release",
                    "content": '{"identifier":"ENG-42","project":{"identifier":"ENG"}}',
                }
            ],
            "model": "gpt-5.1-chat",
        },
    )

    assert response.status_code == 200
    assert "event: message.delta" in response.text
    assert "event: proposal.created" in response.text
    assert "event: message.completed" in response.text
    assert "data: [DONE]" in response.text
    assert [(message["role"], message["content"]) for message in repository.messages] == [
        ("user", "Add a ready-for-review comment."),
        ("assistant", "I can draft that 【1】. Recheck 【1】, not 【99】. items[1]."),
    ]
    assert repository.messages[1]["citations"] == [
        {
            "citation_id": 1,
            "object_type": "issue",
            "object_id": str(issue_id),
            "project_id": str(project_id),
            "project_identifier": "ENG",
            "sequence_id": 42,
            "title": "Release",
        }
    ]
    assert f'"object_id":"{issue_id}"' in response.text
    assert len(repository.actions) == 1
    assert repository.actions[0]["status"] == "pending"
    assert repository.actions[0]["action_name"] == "create_comment"
    assert repository.actions[0]["message_id"] == repository.messages[1]["id"]


def test_chat_accepts_current_model_alias(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    received: dict[str, str | None] = {}

    async def stream_chat(**values):
        received["deployment"] = values["deployment"]
        yield AzureStreamEvent(kind="delta", delta="Done")

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Summarize this workspace.",
            "context_type": "workspace",
            "context_items": [],
            "current_model": "backend-selected-deployment",
        },
    )

    assert response.status_code == 200
    assert received["deployment"] == "backend-selected-deployment"
    assert "event: message.completed" in response.text


def test_chat_uses_new_citation_ids_and_strips_old_markers_from_history(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    issue_id = uuid4()
    repository.messages.append(
        {
            "id": uuid4(),
            "role": "assistant",
            "content": "Earlier answer 【1】. items[1].",
            "citations": [{"citation_id": 1}],
        }
    )
    received: dict[str, object] = {}

    async def stream_chat(**values):
        received.update(values)
        yield AzureStreamEvent(kind="delta", delta="Current answer 【2】.")

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "What changed?",
            "context_type": "workspace",
            "context_items": [
                {
                    "object_type": "issue",
                    "object_id": str(issue_id),
                    "project_id": str(project_id),
                    "title": "Authentication",
                    "content": '{"identifier":"ENG-42","project":{"identifier":"ENG"}}',
                }
            ],
        },
    )

    assert response.status_code == 200
    assert received["citation_start"] == 2
    assert received["history"] == [{"role": "assistant", "content": "Earlier answer . items[1]."}]
    assert repository.messages[-1]["citations"] == [
        {
            "citation_id": 2,
            "object_type": "issue",
            "object_id": str(issue_id),
            "project_id": str(project_id),
            "project_identifier": "ENG",
            "sequence_id": 42,
            "title": "Authentication",
        }
    ]


def test_chat_rejects_tool_ids_outside_permission_checked_context(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    issue_id = uuid4()

    async def stream_chat(**_values):
        yield AzureStreamEvent(
            kind="tool_call",
            action_name="edit_issue_description",
            arguments={
                "project_id": str(project_id),
                "issue_id": str(uuid4()),
                "description_html": "<p>Changed</p>",
            },
        )

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Change the description.",
            "context_type": "work_item",
            "project_id": str(project_id),
            "issue_id": str(issue_id),
            "context_items": [],
            "model": "gpt-5.1-chat",
        },
    )

    assert "event: error" in response.text
    assert repository.actions == []
    assert not any(message["role"] == "assistant" for message in repository.messages)


def test_chat_accepts_create_issue_with_permission_checked_catalog(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    state_id = uuid4()

    async def stream_chat(**values):
        assert values["catalog"][0].id == project_id
        yield AzureStreamEvent(
            kind="tool_call",
            action_name="create_issue",
            arguments={
                "project_id": str(project_id),
                "name": "Prepare launch checklist",
                "description_html": "<p>Coordinate launch tasks.</p>",
                "state_id": str(state_id),
                "priority": "high",
                "start_date": None,
                "target_date": None,
                "assignee_ids": [],
                "label_ids": [],
                "cycle_id": None,
                "module_ids": [],
            },
        )

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Create a high priority launch checklist.",
            "context_type": "workspace",
            "context_items": [],
            "catalog": [
                {"id": str(project_id), "kind": "project", "name": "Launch", "identifier": "LAU"},
                {
                    "id": str(state_id),
                    "kind": "state",
                    "name": "Todo",
                    "project_id": str(project_id),
                    "group": "unstarted",
                },
            ],
        },
    )

    assert response.status_code == 200
    assert repository.actions[0]["action_name"] == "create_issue"
    assert repository.actions[0]["arguments"]["state_id"] == str(state_id)


def test_chat_rejects_bulk_action_with_unlisted_work_item(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    listed_issue_id = uuid4()

    async def stream_chat(**_values):
        yield AzureStreamEvent(
            kind="tool_call",
            action_name="bulk_update_issues",
            arguments={
                "project_id": str(project_id),
                "issue_ids": [str(listed_issue_id), str(uuid4())],
                "fields_to_update": ["priority"],
                "state_id": None,
                "priority": "high",
                "start_date": None,
                "target_date": None,
                "assignee_ids": None,
                "label_ids": None,
                "cycle_id": None,
                "module_ids": None,
            },
        )

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Set both listed items to high priority.",
            "context_type": "workspace",
            "context_items": [
                {
                    "object_type": "issue",
                    "object_id": str(listed_issue_id),
                    "project_id": str(project_id),
                    "title": "Listed item",
                    "content": '{"identifier":"ENG-7","project":{"identifier":"ENG"}}',
                }
            ],
            "catalog": [{"id": str(project_id), "kind": "project", "name": "Engineering"}],
        },
    )

    assert "event: error" in response.text
    assert repository.actions == []


def test_chat_rejects_bulk_action_from_truncated_report(
    client,
    settings,
    repository: FakeRepository,
    azure: FakeAzure,
    monkeypatch,
) -> None:
    project_id = uuid4()
    issue_id = uuid4()

    async def stream_chat(**_values):
        yield AzureStreamEvent(
            kind="tool_call",
            action_name="archive_issues",
            arguments={"project_id": str(project_id), "issue_ids": [str(issue_id)]},
        )

    monkeypatch.setattr(azure, "stream_chat", stream_chat)
    response = signed_json(
        client,
        settings,
        "POST",
        "/internal/chat",
        {
            "thread_id": str(repository.thread_id),
            "workspace_id": str(repository.workspace_id),
            "user_id": str(repository.user_id),
            "prompt": "Archive all completed work items.",
            "context_type": "workspace",
            "context_items": [
                {
                    "object_type": "issue",
                    "object_id": str(issue_id),
                    "project_id": str(project_id),
                    "title": "Completed item",
                    "content": '{"identifier":"ENG-7","project":{"identifier":"ENG"}}',
                }
            ],
            "catalog": [{"id": str(project_id), "kind": "project", "name": "Engineering"}],
            "report_result": {
                "total": 2,
                "items": [{"object_id": str(issue_id)}],
                "items_truncated": True,
            },
        },
    )

    assert "event: error" in response.text
    assert repository.actions == []


def test_action_completion_is_idempotent(client, settings, repository: FakeRepository) -> None:
    repository.actions.append(
        {
            "id": repository.action_id,
            "workspace_id": repository.workspace_id,
            "user_id": repository.user_id,
            "status": "pending",
        }
    )
    payload = {
        "workspace_id": str(repository.workspace_id),
        "user_id": str(repository.user_id),
        "result": {"comment_id": str(uuid4())},
    }
    url = f"/internal/actions/{repository.action_id}/complete"

    first = signed_json(client, settings, "POST", url, payload)
    second = signed_json(client, settings, "POST", url, {**payload, "result": {"ignored": True}})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["result"] == payload["result"]
