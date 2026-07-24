from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.test import override_settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIRequestFactory

from plane.crete_ai.views import (
    CreteAIWorkspaceAPIView,
    EventStreamRenderer,
    ThreadChatEndpoint,
    ThreadDetailEndpoint,
    _acquire_chat_lock,
    _release_chat_lock,
    _thread_scope_is_current,
)


pytestmark = pytest.mark.unit


def test_browser_ai_endpoints_enforce_session_csrf():
    assert CreteAIWorkspaceAPIView.authentication_classes == [SessionAuthentication]


def test_chat_endpoint_accepts_event_stream_requests():
    view = ThreadChatEndpoint()
    view.format_kwarg = None
    request = view.initialize_request(APIRequestFactory().post("/", {}, format="json", HTTP_ACCEPT="text/event-stream"))

    renderer, media_type = view.perform_content_negotiation(request)

    assert isinstance(renderer, EventStreamRenderer)
    assert media_type == "text/event-stream"
    assert renderer.render({"error": "invalid"}) == b'{"error":"invalid"}'


@patch("plane.crete_ai.views.get_redis_connection")
def test_chat_lock_uses_unique_owner_token_and_atomic_release(get_connection):
    connection = MagicMock()
    connection.set.return_value = True
    get_connection.return_value = connection

    token = _acquire_chat_lock("chat-key", 30)
    _release_chat_lock("chat-key", token)

    connection.set.assert_called_once_with("chat-key", token, nx=True, ex=30)
    script, key_count, key, released_token = connection.eval.call_args.args
    assert "redis.call('get', KEYS[1]) == ARGV[1]" in script
    assert (key_count, key, released_token) == (1, "chat-key", token)


def test_thread_scope_must_match_current_memberships():
    project_id = uuid4()
    restricted_project_id = uuid4()
    allowed_projects = {str(project_id), str(restricted_project_id)}
    restricted_projects = {str(restricted_project_id)}
    thread = {
        "authorization_version": 1,
        "authorized_project_ids": [str(project_id), str(restricted_project_id)],
        "restricted_project_ids": [str(restricted_project_id)],
    }

    assert _thread_scope_is_current(thread, allowed_projects, restricted_projects)

    assert not _thread_scope_is_current(thread, {str(project_id)}, set())

    thread["authorization_version"] = 0
    assert not _thread_scope_is_current(thread, set(), set())


@patch("plane.crete_ai.views.sanitize_thread_citations")
@patch("plane.crete_ai.views._current_thread_scope")
@patch("plane.crete_ai.views.CreteAIClient")
def test_thread_detail_reauthorizes_persisted_citations(
    client_class,
    current_thread_scope,
    sanitize_thread_citations,
):
    project_id = uuid4()
    workspace = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    thread = {
        "authorization_version": 1,
        "authorized_project_ids": [str(project_id)],
        "restricted_project_ids": [],
        "messages": [],
    }
    client_class.return_value.get_thread.return_value = thread
    current_thread_scope.return_value = ({str(project_id)}, set())
    sanitize_thread_citations.return_value = {**thread, "citations_sanitized": True}
    view = ThreadDetailEndpoint()
    view.get_workspace = MagicMock(return_value=workspace)

    response = view.get(SimpleNamespace(user=user), "tech", uuid4())

    assert response.status_code == 200
    assert response.data["citations_sanitized"] is True
    sanitize_thread_citations.assert_called_once_with(
        thread,
        workspace,
        user,
        {str(project_id)},
        set(),
    )


@override_settings(CRETE_AI_REPORTING_ENABLED=True)
def test_chat_executes_permission_scoped_report_before_streaming():
    project_id = uuid4()
    workspace = SimpleNamespace(id=uuid4(), timezone="UTC")
    user = SimpleNamespace(id=uuid4())
    thread_id = uuid4()
    thread = {
        "authorization_version": 1,
        "authorized_project_ids": [str(project_id)],
        "restricted_project_ids": [],
        "context_type": "workspace",
    }
    client = MagicMock()
    client.get_thread.return_value = thread
    client.plan_report.return_value = {"mode": "report", "filters": {}}
    upstream = MagicMock(status_code=200, headers={"Content-Type": "text/event-stream"})
    client.stream_chat.return_value = upstream
    catalog = [{"id": str(project_id), "kind": "project", "name": "Engineering"}]
    report = {"total": 3, "groups": [{"name": "Started", "count": 3}]}
    context_items = [{"object_type": "issue", "object_id": str(uuid4())}]
    view = ThreadChatEndpoint()
    view.get_workspace = MagicMock(return_value=workspace)

    with (
        patch("plane.crete_ai.views.CreteAIClient", return_value=client),
        patch("plane.crete_ai.views._current_thread_scope", return_value=({str(project_id)}, set())),
        patch("plane.crete_ai.views.validate_context"),
        patch("plane.crete_ai.views._acquire_chat_lock", return_value="lock-token"),
        patch("plane.crete_ai.views.should_plan_report", return_value=True),
        patch("plane.crete_ai.views.build_report_catalog", return_value=(catalog, [str(project_id)])),
        patch("plane.crete_ai.views.execute_report", return_value=(report, context_items)) as execute_report,
        patch("plane.crete_ai.views.collect_chat_context") as collect_chat_context,
    ):
        response = view.post(
            SimpleNamespace(
                user=user,
                data={"prompt": "Count unresolved work by status.", "context_type": "workspace"},
            ),
            "tech",
            thread_id,
        )

    assert response.status_code == 200
    collect_chat_context.assert_not_called()
    execute_report.assert_called_once()
    payload = client.stream_chat.call_args.args[0]
    assert payload["context_type"] == "workspace"
    assert payload["context_items"] == context_items
    assert payload["report_result"] == report


@override_settings(CRETE_AI_REPORTING_ENABLED=False)
def test_reporting_gate_preserves_old_ai_chat_contract_during_rollout():
    project_id = uuid4()
    workspace = SimpleNamespace(id=uuid4(), timezone="UTC")
    user = SimpleNamespace(id=uuid4())
    client = MagicMock()
    client.get_thread.return_value = {
        "authorization_version": 1,
        "authorized_project_ids": [str(project_id)],
        "restricted_project_ids": [],
        "context_type": "workspace",
    }
    upstream = MagicMock(status_code=200, headers={"Content-Type": "text/event-stream"})
    client.stream_chat.return_value = upstream
    view = ThreadChatEndpoint()
    view.get_workspace = MagicMock(return_value=workspace)
    context_items = [{"object_type": "issue", "object_id": str(uuid4())}]

    with (
        patch("plane.crete_ai.views.CreteAIClient", return_value=client),
        patch("plane.crete_ai.views._current_thread_scope", return_value=({str(project_id)}, set())),
        patch("plane.crete_ai.views.validate_context"),
        patch("plane.crete_ai.views._acquire_chat_lock", return_value="lock-token"),
        patch("plane.crete_ai.views.should_plan_report") as should_plan_report,
        patch("plane.crete_ai.views.collect_chat_context", return_value=(context_items, [project_id])),
    ):
        response = view.post(
            SimpleNamespace(
                user=user,
                data={"prompt": "Count unresolved work.", "context_type": "workspace"},
            ),
            "tech",
            uuid4(),
        )

    assert response.status_code == 200
    should_plan_report.assert_not_called()
    client.plan_report.assert_not_called()
    payload = client.stream_chat.call_args.args[0]
    assert "report_result" not in payload
