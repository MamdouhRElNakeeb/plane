import json
from uuid import UUID

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from plane.api.serializers import IssueCommentCreateSerializer, IssueSerializer
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.webhook_task import model_activity
from plane.crete_ai.models import ConfirmedAction
from plane.db.models import Issue, ProjectMember
from plane.utils.content_validator import validate_html_content


class ActionValidationError(Exception):
    pass


SUPPORTED_ACTIONS = {
    "create_comment",
    "edit_issue_description",
    "create_subtask",
}
ACTION_ALIASES = {
    "update_description": "edit_issue_description",
}


def _uuid(value, field_name):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ActionValidationError(f"Invalid {field_name}") from exc


def _action_data(action):
    if isinstance(action.get("action"), dict):
        action = action["action"]
    action_type = action.get("type") or action.get("action_type") or action.get("action_name") or action.get("name")
    action_type = ACTION_ALIASES.get(action_type, action_type)
    payload = action.get("payload") or action.get("arguments") or {}
    if action_type not in SUPPORTED_ACTIONS or not isinstance(payload, dict):
        raise ActionValidationError("Unsupported action")
    return action_type, payload


def _project_for_action(workspace, user, project_id):
    project_id = _uuid(project_id, "project_id")
    membership = (
        ProjectMember.objects.select_related("project")
        .filter(
            workspace=workspace,
            project_id=project_id,
            member=user,
            role__in=[20, 15],
            is_active=True,
            project__archived_at__isnull=True,
        )
        .first()
    )
    if membership is None:
        raise ActionValidationError("Project not found or action is not permitted")
    return membership.project


def _issue_for_action(workspace, project, issue_id, field_name="issue_id"):
    issue_id = _uuid(issue_id, field_name)
    issue = (
        Issue.issue_objects.select_related("project", "state")
        .filter(id=issue_id, workspace=workspace, project=project)
        .first()
    )
    if issue is None:
        raise ActionValidationError("Work item not found")
    return issue


def _sanitize_html(value, field_name):
    if not isinstance(value, str):
        raise ActionValidationError(f"Invalid {field_name}")
    if len(value) > settings.CRETE_AI_ACTION_HTML_CHARS:
        raise ActionValidationError(f"{field_name} is too long")
    is_valid, _, sanitized_html = validate_html_content(value)
    if not is_valid:
        raise ActionValidationError(f"Invalid {field_name}")
    return sanitized_html if sanitized_html is not None else value


def _schedule_activity(**kwargs):
    transaction.on_commit(lambda: issue_activity.delay(**kwargs))


def _schedule_model_activity(**kwargs):
    transaction.on_commit(lambda: model_activity.delay(**kwargs))


def _create_comment(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    issue = _issue_for_action(workspace, project, payload.get("issue_id"))
    comment_html = _sanitize_html(payload.get("comment_html"), "comment_html")
    serializer = IssueCommentCreateSerializer(data={"comment_html": comment_html})
    if not serializer.is_valid():
        raise ActionValidationError("Invalid comment")
    comment = serializer.save(project=project, issue=issue, actor=user)
    _schedule_activity(
        type="comment.activity.created",
        requested_data=json.dumps(serializer.data, cls=DjangoJSONEncoder),
        actor_id=str(user.id),
        issue_id=str(issue.id),
        project_id=str(project.id),
        current_instance=None,
        epoch=int(timezone.now().timestamp()),
        notification=True,
    )
    _schedule_model_activity(
        model_name="issue_comment",
        model_id=str(comment.id),
        requested_data=dict(serializer.data),
        current_instance=None,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return {
        "action_type": "create_comment",
        "comment_id": str(comment.id),
        "issue_id": str(issue.id),
        "project_id": str(project.id),
    }


def _edit_issue_description(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    issue = _issue_for_action(workspace, project, payload.get("issue_id"))
    description_html = _sanitize_html(payload.get("description_html"), "description_html")
    current_instance = json.dumps(IssueSerializer(issue).data, cls=DjangoJSONEncoder)
    serializer = IssueSerializer(
        issue,
        data={"description_html": description_html},
        context={"project_id": project.id, "workspace_id": workspace.id},
        partial=True,
    )
    if not serializer.is_valid():
        raise ActionValidationError("Invalid work item description")
    serializer.save()
    _schedule_activity(
        type="issue.activity.updated",
        requested_data=json.dumps({"description_html": description_html}),
        actor_id=str(user.id),
        issue_id=str(issue.id),
        project_id=str(project.id),
        current_instance=current_instance,
        epoch=int(timezone.now().timestamp()),
        notification=True,
    )
    _schedule_model_activity(
        model_name="issue",
        model_id=str(issue.id),
        requested_data={"description_html": description_html},
        current_instance=current_instance,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return {
        "action_type": "edit_issue_description",
        "issue_id": str(issue.id),
        "project_id": str(project.id),
    }


def _create_subtask(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    parent = _issue_for_action(
        workspace,
        project,
        payload.get("parent_issue_id"),
        field_name="parent_issue_id",
    )
    description_html = _sanitize_html(payload.get("description_html", ""), "description_html")
    serializer = IssueSerializer(
        data={
            "name": payload.get("name"),
            "description_html": description_html,
            "parent": str(parent.id),
        },
        context={
            "project_id": project.id,
            "workspace_id": workspace.id,
            "default_assignee_id": project.default_assignee_id,
        },
    )
    if not serializer.is_valid():
        raise ActionValidationError("Invalid subtask")
    issue = serializer.save()
    _schedule_activity(
        type="issue.activity.created",
        requested_data=json.dumps(
            {
                "name": issue.name,
                "description_html": description_html,
                "parent": str(parent.id),
            }
        ),
        actor_id=str(user.id),
        issue_id=str(issue.id),
        project_id=str(project.id),
        current_instance=None,
        epoch=int(timezone.now().timestamp()),
        notification=True,
    )
    _schedule_model_activity(
        model_name="issue",
        model_id=str(issue.id),
        requested_data={
            "name": issue.name,
            "description_html": description_html,
            "parent": str(parent.id),
        },
        current_instance=None,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return {
        "action_type": "create_subtask",
        "issue_id": str(issue.id),
        "parent_issue_id": str(parent.id),
        "project_id": str(project.id),
    }


ACTION_EXECUTORS = {
    "create_comment": _create_comment,
    "edit_issue_description": _edit_issue_description,
    "create_subtask": _create_subtask,
}


def execute_confirmed_action(action_id, action, workspace, user):
    action_id = _uuid(action_id, "action_id")
    action_type, payload = _action_data(action)

    action_workspace_id = action.get("workspace_id")
    action_user_id = action.get("user_id")
    if action_workspace_id is not None and str(action_workspace_id) != str(workspace.id):
        raise ActionValidationError("Action does not belong to this workspace")
    if action_user_id is not None and str(action_user_id) != str(user.id):
        raise ActionValidationError("Action does not belong to this user")

    # This fresh check is intentionally performed even for an idempotent retry.
    _project_for_action(workspace, user, payload.get("project_id"))

    with transaction.atomic():
        record, _ = ConfirmedAction.objects.select_for_update().get_or_create(
            action_id=action_id,
            defaults={
                "workspace": workspace,
                "user": user,
                "action_type": action_type,
            },
        )
        if record.workspace_id != workspace.id or record.user_id != user.id:
            raise ActionValidationError("Action ownership mismatch")
        if record.action_type != action_type:
            raise ActionValidationError("Action type mismatch")
        if record.status == "completed":
            return record.result

        result = ACTION_EXECUTORS[action_type](workspace, user, payload)
        record.status = "completed"
        record.result = result
        record.save(update_fields=["status", "result", "updated_at"])
    return result
