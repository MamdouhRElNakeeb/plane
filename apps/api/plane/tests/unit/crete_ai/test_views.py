from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.authentication import SessionAuthentication

from plane.crete_ai.views import (
    CreteAIWorkspaceAPIView,
    _acquire_chat_lock,
    _release_chat_lock,
    _thread_scope_is_current,
)


pytestmark = pytest.mark.unit


def test_browser_ai_endpoints_enforce_session_csrf():
    assert CreteAIWorkspaceAPIView.authentication_classes == [SessionAuthentication]


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
