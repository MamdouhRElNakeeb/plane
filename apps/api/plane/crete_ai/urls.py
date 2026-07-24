from django.urls import path

from plane.crete_ai.views import (
    ActionConfirmEndpoint,
    ThreadChatEndpoint,
    ThreadDetailEndpoint,
    ThreadListCreateEndpoint,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/threads/",
        ThreadListCreateEndpoint.as_view(),
        name="crete-ai-threads",
    ),
    path(
        "workspaces/<str:slug>/threads/<uuid:thread_id>/",
        ThreadDetailEndpoint.as_view(),
        name="crete-ai-thread",
    ),
    path(
        "workspaces/<str:slug>/threads/<uuid:thread_id>/chat/",
        ThreadChatEndpoint.as_view(),
        name="crete-ai-chat",
    ),
    path(
        "workspaces/<str:slug>/actions/<uuid:action_id>/confirm/",
        ActionConfirmEndpoint.as_view(),
        name="crete-ai-action-confirm",
    ),
]
