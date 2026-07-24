from uuid import uuid4

from django.conf import settings
from django.http import StreamingHttpResponse
from django_redis import get_redis_connection
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.crete_ai.actions import ActionValidationError, execute_confirmed_action
from plane.crete_ai.client import AIServiceError, CreteAIClient
from plane.crete_ai.context import (
    ContextValidationError,
    collect_chat_context,
    get_allowed_project_ids,
    get_restricted_guest_project_ids,
    get_workspace_for_user,
    validate_context,
)
from plane.crete_ai.serializers import ChatRequestSerializer, ThreadCreateSerializer
from plane.crete_ai.throttles import CreteAIChatThrottle, CreteAIUserThrottle


class EventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode()
        return JSONRenderer().render(data, accepted_media_type="application/json", renderer_context=renderer_context)


def _service_error_response():
    return Response(
        {"error": "The AI assistant is temporarily unavailable"},
        status=status.HTTP_502_BAD_GATEWAY,
    )


def _acquire_chat_lock(key, timeout):
    token = uuid4().hex
    acquired = get_redis_connection("default").set(key, token, nx=True, ex=timeout)
    return token if acquired else None


def _release_chat_lock(key, token):
    get_redis_connection("default").eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


def _current_thread_scope(workspace, user):
    return (
        {str(value) for value in get_allowed_project_ids(workspace, user)},
        {str(value) for value in get_restricted_guest_project_ids(workspace, user)},
    )


def _thread_scope_is_current(thread, current_project_ids, current_restricted_ids):
    if thread.get("authorization_version") != 1:
        return False
    stored_project_ids = {str(value) for value in thread.get("authorized_project_ids", [])}
    stored_restricted_ids = {str(value) for value in thread.get("restricted_project_ids", [])}
    return current_project_ids == stored_project_ids and current_restricted_ids == stored_restricted_ids


class CreteAIWorkspaceAPIView(BaseAPIView):
    authentication_classes = [SessionAuthentication]
    throttle_classes = [CreteAIUserThrottle]

    def get_workspace(self, request, slug):
        return get_workspace_for_user(slug, request.user)

    def handle_context_error(self, exc):
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ThreadListCreateEndpoint(CreteAIWorkspaceAPIView):
    def get(self, request, slug):
        try:
            workspace = self.get_workspace(request, slug)
            data = CreteAIClient().list_threads(workspace.id, request.user.id)
            current_project_ids, current_restricted_ids = _current_thread_scope(workspace, request.user)
        except ContextValidationError:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except AIServiceError:
            return _service_error_response()
        data["threads"] = [
            thread
            for thread in data.get("threads", [])
            if _thread_scope_is_current(thread, current_project_ids, current_restricted_ids)
        ]
        return Response(data)

    def post(self, request, slug):
        serializer = ThreadCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            workspace = self.get_workspace(request, slug)
            validate_context(
                workspace,
                request.user,
                data["context_type"],
                project_id=data.get("project_id"),
                issue_id=data.get("issue_id"),
            )
            payload = {
                "authorization_version": 1,
                "context_type": data["context_type"],
                "authorized_project_ids": [str(value) for value in get_allowed_project_ids(workspace, request.user)],
                "restricted_project_ids": [
                    str(value) for value in get_restricted_guest_project_ids(workspace, request.user)
                ],
                **({"title": data["title"]} if "title" in data else {}),
                **({"project_id": str(data["project_id"])} if data.get("project_id") else {}),
                **({"issue_id": str(data["issue_id"])} if data.get("issue_id") else {}),
            }
            result = CreteAIClient().create_thread(workspace.id, request.user.id, payload)
        except ContextValidationError as exc:
            return self.handle_context_error(exc)
        except AIServiceError:
            return _service_error_response()
        return Response(result, status=status.HTTP_201_CREATED)


class ThreadDetailEndpoint(CreteAIWorkspaceAPIView):
    def get(self, request, slug, thread_id):
        try:
            workspace = self.get_workspace(request, slug)
            data = CreteAIClient().get_thread(workspace.id, thread_id, request.user.id)
            current_project_ids, current_restricted_ids = _current_thread_scope(workspace, request.user)
        except ContextValidationError:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except AIServiceError:
            return _service_error_response()
        if not _thread_scope_is_current(data, current_project_ids, current_restricted_ids):
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)

    def delete(self, request, slug, thread_id):
        try:
            workspace = self.get_workspace(request, slug)
            CreteAIClient().delete_thread(workspace.id, thread_id, request.user.id)
        except ContextValidationError:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except AIServiceError:
            return _service_error_response()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ThreadChatEndpoint(CreteAIWorkspaceAPIView):
    renderer_classes = [EventStreamRenderer]
    throttle_classes = [CreteAIUserThrottle, CreteAIChatThrottle]

    def post(self, request, slug, thread_id):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        chat_lock_key = None
        chat_lock_token = None
        try:
            workspace = self.get_workspace(request, slug)
            client = CreteAIClient()
            thread = client.get_thread(workspace.id, thread_id, request.user.id)
            current_project_ids, current_restricted_ids = _current_thread_scope(workspace, request.user)
            if not _thread_scope_is_current(thread, current_project_ids, current_restricted_ids):
                return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
            validate_context(
                workspace,
                request.user,
                thread.get("context_type") or "general",
                project_id=thread.get("project_id"),
                issue_id=thread.get("issue_id"),
            )
            chat_lock_key = f"crete-ai-chat:{workspace.id}:{request.user.id}"
            chat_lock_token = _acquire_chat_lock(
                chat_lock_key,
                settings.CRETE_AI_READ_TIMEOUT + 30,
            )
            if not chat_lock_token:
                return Response(
                    {"error": "Another assistant response is already in progress"},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            context_items, _ = collect_chat_context(
                client=client,
                workspace=workspace,
                user=request.user,
                prompt=data["prompt"],
                context_type=data["context_type"],
                project_id=data.get("project_id"),
                issue_id=data.get("issue_id"),
            )
            payload = {
                "thread_id": str(thread_id),
                "workspace_id": str(workspace.id),
                "user_id": str(request.user.id),
                "prompt": data["prompt"],
                "context_type": data["context_type"],
                "context_items": context_items,
                "current_model": settings.CRETE_AI_CHAT_MODEL,
                **({"project_id": str(data["project_id"])} if data.get("project_id") else {}),
                **({"issue_id": str(data["issue_id"])} if data.get("issue_id") else {}),
            }
            upstream = client.stream_chat(payload)
        except ContextValidationError as exc:
            if chat_lock_key and chat_lock_token:
                _release_chat_lock(chat_lock_key, chat_lock_token)
            return self.handle_context_error(exc)
        except AIServiceError:
            if chat_lock_key and chat_lock_token:
                _release_chat_lock(chat_lock_key, chat_lock_token)
            return _service_error_response()

        def stream():
            try:
                yield from upstream.iter_content(chunk_size=8192)
            finally:
                upstream.close()
                _release_chat_lock(chat_lock_key, chat_lock_token)

        response = StreamingHttpResponse(
            stream(),
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "text/event-stream"),
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response


class ActionConfirmEndpoint(CreteAIWorkspaceAPIView):
    def post(self, request, slug, action_id):
        try:
            workspace = self.get_workspace(request, slug)
            client = CreteAIClient()
            action = client.get_action(action_id, workspace.id, request.user.id)
            result = execute_confirmed_action(
                action_id,
                action,
                workspace,
                request.user,
            )
            client.complete_action(
                action_id,
                workspace.id,
                request.user.id,
                result,
            )
        except ContextValidationError:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except ActionValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AIServiceError:
            return _service_error_response()
        return Response(result, status=status.HTTP_200_OK)
