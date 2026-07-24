import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.test import override_settings

from plane.crete_ai.context import (
    ContextValidationError,
    _bound_items,
    collect_chat_context,
    sanitize_thread_citations,
    validate_context,
)


pytestmark = pytest.mark.unit


@patch("plane.crete_ai.context.get_allowed_project_ids", return_value=[])
def test_project_context_requires_active_project_membership(mock_allowed):
    with pytest.raises(ContextValidationError, match="Project not found"):
        validate_context(
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4()),
            "project",
            project_id=uuid4(),
        )
    mock_allowed.assert_called_once()


@patch("plane.crete_ai.context.validate_context")
def test_general_context_does_not_retrieve_plane_content(mock_validate):
    project_id = uuid4()
    mock_validate.return_value = ([project_id], None)
    client = MagicMock()

    context_items, project_ids = collect_chat_context(
        client=client,
        workspace=SimpleNamespace(id=uuid4()),
        user=SimpleNamespace(id=uuid4()),
        prompt="Explain agile planning",
        context_type="general",
    )

    assert context_items == []
    assert project_ids == [project_id]
    client.retrieve.assert_not_called()


@override_settings(CRETE_AI_CONTEXT_ITEM_LIMIT=2, CRETE_AI_CONTEXT_TOTAL_CHARS=9)
def test_context_items_are_bounded_by_count_and_total_text():
    items = [
        {"title": "12345", "content": "67890"},
        {"title": "abc", "content": "def"},
        {"title": "ignored", "content": "ignored"},
    ]

    bounded = _bound_items(items)

    assert len(bounded) == 1
    assert len(bounded[0]["title"]) + len(bounded[0]["content"]) <= 9


@override_settings(CRETE_AI_CONTEXT_ITEM_LIMIT=2, CRETE_AI_CONTEXT_TOTAL_CHARS=140)
def test_bounded_issue_context_keeps_citation_metadata_as_valid_json():
    content = json.dumps(
        {
            "identifier": "ENG-42",
            "description": "x" * 500,
            "project": {"id": str(uuid4()), "name": "Engineering", "identifier": "ENG"},
        },
        separators=(",", ":"),
    )

    bounded = _bound_items([{"title": "Authentication issue", "content": content}])

    assert len(bounded) == 1
    assert len(bounded[0]["title"]) + len(bounded[0]["content"]) <= 140
    bounded_details = json.loads(bounded[0]["content"])
    assert bounded_details["identifier"] == "ENG-42"
    assert bounded_details["project"]["identifier"] == "ENG"
    assert bounded_details["truncated_context"]


@override_settings(
    CRETE_AI_RETRIEVAL_LIMIT=8,
    CRETE_AI_CONTEXT_ITEM_LIMIT=8,
    CRETE_AI_CONTEXT_ITEM_CHARS=100,
    CRETE_AI_CONTEXT_TOTAL_CHARS=1000,
    CRETE_AI_CONTEXT_COMMENT_LIMIT=5,
    CRETE_AI_CONTEXT_COMMENT_CHARS=100,
)
@patch("plane.crete_ai.context.get_restricted_guest_project_ids", return_value=set())
@patch("plane.crete_ai.context.validate_context")
@patch("plane.crete_ai.context.Issue.issue_objects")
def test_retrieval_results_are_refetched_with_allowed_projects(
    issue_manager,
    mock_validate,
    _mock_restricted_projects,
):
    allowed_project_id = uuid4()
    allowed_issue_id = uuid4()
    unauthorized_issue_id = uuid4()
    project = SimpleNamespace(
        id=allowed_project_id,
        name="Allowed",
        identifier="OK",
    )
    issue = SimpleNamespace(
        id=allowed_issue_id,
        name="Allowed issue",
        description_stripped="Current database description",
        sequence_id=7,
        priority="high",
        state_id=None,
        project_id=allowed_project_id,
        project=project,
        updated_at=datetime.now(timezone.utc),
    )
    issue_manager.select_related.return_value.filter.return_value.filter.return_value = [issue]
    mock_validate.return_value = ([allowed_project_id], None)
    client = MagicMock()
    client.retrieve.return_value = {
        "matches": [
            {"object_id": str(unauthorized_issue_id)},
            {"object_id": str(allowed_issue_id)},
        ]
    }
    workspace = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())

    context_items, project_ids = collect_chat_context(
        client=client,
        workspace=workspace,
        user=user,
        prompt="status",
        context_type="workspace",
    )

    assert project_ids == [allowed_project_id]
    assert [item["object_id"] for item in context_items] == [str(allowed_issue_id)]
    context_details = json.loads(context_items[0]["content"])
    assert context_details["identifier"] == "OK-7"
    assert context_details["project"]["identifier"] == "OK"
    assert context_details["description"] == "Current database description"
    filter_kwargs = issue_manager.select_related.return_value.filter.call_args.kwargs
    assert filter_kwargs["project_id__in"] == [allowed_project_id]
    retrieve_payload = client.retrieve.call_args.args[0]
    assert retrieve_payload["allowed_project_ids"] == [str(allowed_project_id)]


@patch("plane.crete_ai.context.get_restricted_guest_project_ids")
@patch("plane.crete_ai.context.validate_context")
@patch("plane.crete_ai.context.Issue.issue_objects")
def test_restricted_guests_only_refetch_issues_they_created(
    issue_manager,
    mock_validate,
    mock_restricted_projects,
):
    project_id = uuid4()
    issue_id = uuid4()
    user_id = uuid4()
    candidate_queryset = MagicMock()
    candidate_queryset.filter.return_value = []
    issue_manager.select_related.return_value.filter.return_value = candidate_queryset
    mock_validate.return_value = ([project_id], None)
    mock_restricted_projects.return_value = {project_id}
    client = MagicMock()
    client.retrieve.return_value = {"matches": [{"object_id": str(issue_id)}]}

    context_items, _ = collect_chat_context(
        client=client,
        workspace=SimpleNamespace(id=uuid4()),
        user=SimpleNamespace(id=user_id),
        prompt="status",
        context_type="workspace",
    )

    assert context_items == []
    visibility_filter = candidate_queryset.filter.call_args.args[0]
    assert "project_id__in" in str(visibility_filter)
    assert "created_by" in str(visibility_filter)


@override_settings(CRETE_AI_CONTEXT_ITEM_LIMIT=8, CRETE_AI_CONTEXT_ITEM_CHARS=100)
@patch("plane.crete_ai.context.Issue.issue_objects")
def test_persisted_citations_are_reauthorized_and_refreshed(issue_manager):
    project_id = uuid4()
    issue_id = uuid4()
    removed_issue_id = uuid4()
    issue = SimpleNamespace(
        id=issue_id,
        project_id=project_id,
        project=SimpleNamespace(identifier="ENG"),
        sequence_id=42,
        name="Current issue title",
    )
    issue_manager.select_related.return_value.filter.return_value.filter.return_value = [issue]
    workspace = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    thread = {
        "id": str(uuid4()),
        "messages": [
            {
                "id": str(uuid4()),
                "citations": [
                    {
                        "citation_id": 12,
                        "object_type": "issue",
                        "object_id": str(issue_id),
                        "project_id": str(uuid4()),
                        "project_identifier": "STALE",
                        "sequence_id": 1,
                        "title": "Stale title",
                    },
                    {
                        "citation_id": 3,
                        "object_type": "issue",
                        "object_id": str(removed_issue_id),
                    },
                ],
            }
        ],
    }

    sanitized = sanitize_thread_citations(
        thread,
        workspace,
        user,
        {str(project_id)},
        set(),
    )

    assert sanitized["messages"][0]["citations"] == [
        {
            "citation_id": 12,
            "object_type": "issue",
            "object_id": str(issue_id),
            "project_id": str(project_id),
            "project_identifier": "ENG",
            "sequence_id": 42,
            "title": "Current issue title",
        }
    ]
    filter_kwargs = issue_manager.select_related.return_value.filter.call_args.kwargs
    assert filter_kwargs["workspace"] is workspace
    assert set(filter_kwargs["id__in"]) == {issue_id, removed_issue_id}
