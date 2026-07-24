from django.conf import settings
from rest_framework import serializers


class ThreadCreateSerializer(serializers.Serializer):
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=settings.CRETE_AI_THREAD_TITLE_CHARS,
    )
    context_type = serializers.ChoiceField(
        choices=["general", "workspace", "project", "work_item"],
        default="general",
    )
    project_id = serializers.UUIDField(required=False)
    issue_id = serializers.UUIDField(required=False)


class ChatRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=settings.CRETE_AI_PROMPT_CHARS,
    )
    context_type = serializers.ChoiceField(
        choices=["general", "workspace", "project", "work_item"],
        default="general",
    )
    project_id = serializers.UUIDField(required=False)
    issue_id = serializers.UUIDField(required=False)
