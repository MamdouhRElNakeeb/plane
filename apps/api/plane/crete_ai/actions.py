import json
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from plane.api.serializers import (
    CycleCreateSerializer,
    IssueCommentCreateSerializer,
    IssueSerializer,
    ModuleCreateSerializer,
)
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.webhook_task import model_activity
from plane.crete_ai.models import ConfirmedAction
from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    Label,
    Module,
    ModuleIssue,
    ProjectMember,
    State,
)
from plane.utils.content_validator import validate_html_content


class ActionValidationError(Exception):
    pass


SUPPORTED_ACTIONS = {
    "create_comment",
    "edit_issue_description",
    "create_subtask",
    "create_issue",
    "update_issue",
    "create_module",
    "create_cycle",
    "bulk_update_issues",
    "archive_issues",
}
ACTION_ALIASES = {
    "update_description": "edit_issue_description",
}
SINGLE_UPDATE_FIELDS = {
    "name",
    "description_html",
    "state_id",
    "priority",
    "start_date",
    "target_date",
    "assignee_ids",
    "label_ids",
    "cycle_id",
    "module_ids",
}
BULK_UPDATE_FIELDS = SINGLE_UPDATE_FIELDS - {"name", "description_html"}


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


def _unique_uuids(values, field_name, limit=25):
    if not isinstance(values, list) or not 1 <= len(values) <= limit:
        raise ActionValidationError(f"Invalid {field_name}")
    parsed = [_uuid(value, field_name) for value in values]
    if len(set(parsed)) != len(parsed):
        raise ActionValidationError(f"{field_name} must not contain duplicates")
    return parsed


def _validate_related_ids(project, payload, fields):
    checks = (
        ("state_id", State, False),
        ("cycle_id", Cycle, False),
        ("module_ids", Module, True),
        ("label_ids", Label, True),
    )
    for field, model, is_list in checks:
        if field not in fields:
            continue
        value = payload.get(field)
        values = value if is_list else ([] if value is None else [value])
        parsed = [_uuid(item, field) for item in values or []]
        queryset = model.objects.filter(project=project, id__in=parsed, deleted_at__isnull=True)
        if field in {"cycle_id", "module_ids"}:
            queryset = queryset.filter(archived_at__isnull=True)
        if parsed and queryset.count() != len(set(parsed)):
            raise ActionValidationError(f"One or more {field} values are invalid")
    if "assignee_ids" in fields:
        values = payload.get("assignee_ids") or []
        parsed = [_uuid(item, "assignee_ids") for item in values]
        if parsed and ProjectMember.objects.filter(
            project=project,
            member_id__in=parsed,
            role__gte=15,
            is_active=True,
        ).values("member_id").distinct().count() != len(set(parsed)):
            raise ActionValidationError("One or more assignee_ids values are invalid")


def _issue_data(payload, fields):
    data = {}
    direct_fields = {"name", "start_date", "target_date"}
    for field in direct_fields & fields:
        value = payload.get(field)
        if field == "name" and (not isinstance(value, str) or not value.strip()):
            raise ActionValidationError("Invalid name")
        data[field] = value.strip() if field == "name" else value
    if "description_html" in fields:
        data["description_html"] = _sanitize_html(payload.get("description_html") or "", "description_html")
    if "state_id" in fields:
        if payload.get("state_id") is None:
            raise ActionValidationError("state_id cannot be cleared")
        data["state"] = payload["state_id"]
    if "priority" in fields:
        priority = payload.get("priority") or "none"
        if priority not in {"urgent", "high", "medium", "low", "none"}:
            raise ActionValidationError("Invalid priority")
        data["priority"] = priority
    if "assignee_ids" in fields:
        data["assignees"] = payload.get("assignee_ids") or []
    if "label_ids" in fields:
        data["labels"] = payload.get("label_ids") or []
    return data


def _set_issue_relations(workspace, project, user, issue, payload, fields):
    if "cycle_id" in fields:
        CycleIssue.objects.filter(issue=issue).delete()
        if payload.get("cycle_id"):
            CycleIssue.objects.create(
                cycle_id=payload["cycle_id"],
                issue=issue,
                project=project,
                workspace=workspace,
                created_by=user,
                updated_by=user,
            )
    if "module_ids" in fields:
        ModuleIssue.objects.filter(issue=issue).delete()
        ModuleIssue.objects.bulk_create(
            [
                ModuleIssue(
                    module_id=module_id,
                    issue=issue,
                    project=project,
                    workspace=workspace,
                    created_by=user,
                    updated_by=user,
                )
                for module_id in (payload.get("module_ids") or [])
            ],
            ignore_conflicts=True,
        )


def _issue_context(workspace, project):
    return {
        "project_id": project.id,
        "workspace_id": workspace.id,
        "default_assignee_id": project.default_assignee_id,
    }


def _issue_result(action_type, issue, project):
    return {
        "action_type": action_type,
        "issue_id": str(issue.id),
        "project_id": str(project.id),
    }


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


def _create_issue(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    payload = dict(payload)
    if payload.get("state_id") is None:
        default_state_id = project.default_state_id or (
            State.objects.filter(project=project, default=True).values_list("id", flat=True).first()
        )
        if default_state_id is None:
            raise ActionValidationError("The project has no default state")
        payload["state_id"] = str(default_state_id)
    fields = {
        field
        for field in (
            "name",
            "description_html",
            "state_id",
            "priority",
            "start_date",
            "target_date",
            "assignee_ids",
            "label_ids",
            "cycle_id",
            "module_ids",
        )
        if payload.get(field) is not None
    }
    fields.update({"name", "state_id"})
    _validate_related_ids(project, payload, fields)
    data = _issue_data(payload, fields)
    serializer = IssueSerializer(data=data, context=_issue_context(workspace, project))
    if not serializer.is_valid():
        raise ActionValidationError(f"Invalid work item: {serializer.errors}")
    issue = serializer.save(created_by=user, updated_by=user)
    _set_issue_relations(workspace, project, user, issue, payload, fields)
    _schedule_activity(
        type="issue.activity.created",
        requested_data=json.dumps(data, cls=DjangoJSONEncoder),
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
        requested_data=data,
        current_instance=None,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return _issue_result("create_issue", issue, project)


def _update_one_issue(workspace, user, project, issue, payload, fields):
    start_date = payload.get("start_date") if "start_date" in fields else issue.start_date
    target_date = payload.get("target_date") if "target_date" in fields else issue.target_date
    if start_date is not None and target_date is not None and str(start_date) > str(target_date):
        raise ActionValidationError("Start date cannot exceed target date")
    data = _issue_data(payload, fields)
    current_instance = json.dumps(IssueSerializer(issue).data, cls=DjangoJSONEncoder)
    if data:
        serializer = IssueSerializer(
            issue,
            data=data,
            context=_issue_context(workspace, project),
            partial=True,
        )
        if not serializer.is_valid():
            raise ActionValidationError(f"Invalid work item update: {serializer.errors}")
        serializer.save(updated_by=user)
    _set_issue_relations(workspace, project, user, issue, payload, fields)
    requested_data = {
        **data,
        **({"cycle_id": payload.get("cycle_id")} if "cycle_id" in fields else {}),
        **({"module_ids": payload.get("module_ids") or []} if "module_ids" in fields else {}),
    }
    _schedule_activity(
        type="issue.activity.updated",
        requested_data=json.dumps(requested_data, cls=DjangoJSONEncoder),
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
        requested_data=requested_data,
        current_instance=current_instance,
        actor_id=str(user.id),
        slug=workspace.slug,
    )


def _update_issue(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    issue = _issue_for_action(workspace, project, payload.get("issue_id"))
    fields = set(payload.get("fields_to_update") or [])
    if not fields or not fields.issubset(SINGLE_UPDATE_FIELDS):
        raise ActionValidationError("At least one field must be updated")
    _validate_related_ids(project, payload, fields)
    _update_one_issue(workspace, user, project, issue, payload, fields)
    return _issue_result("update_issue", issue, project)


def _create_module(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    data = {
        key: value
        for key, value in payload.items()
        if key in {"name", "description", "start_date", "target_date"} and value is not None
    }
    serializer = ModuleCreateSerializer(
        data=data,
        context={"project_id": project.id, "workspace_id": workspace.id},
    )
    if not serializer.is_valid():
        raise ActionValidationError(f"Invalid module: {serializer.errors}")
    module = serializer.save(created_by=user, updated_by=user)
    _schedule_model_activity(
        model_name="module",
        model_id=str(module.id),
        requested_data=data,
        current_instance=None,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return {
        "action_type": "create_module",
        "module_id": str(module.id),
        "project_id": str(project.id),
    }


def _create_cycle(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    data = {
        key: value
        for key, value in payload.items()
        if key in {"name", "description", "start_date", "end_date"} and value is not None
    }
    data["project_id"] = str(project.id)
    serializer = CycleCreateSerializer(
        data=data,
        context={"request": SimpleNamespace(user=user), "project": project},
    )
    if not serializer.is_valid():
        raise ActionValidationError(f"Invalid cycle: {serializer.errors}")
    cycle = serializer.save(project=project, created_by=user, updated_by=user)
    _schedule_model_activity(
        model_name="cycle",
        model_id=str(cycle.id),
        requested_data=data,
        current_instance=None,
        actor_id=str(user.id),
        slug=workspace.slug,
    )
    return {
        "action_type": "create_cycle",
        "cycle_id": str(cycle.id),
        "project_id": str(project.id),
    }


def _bulk_update_issues(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    issue_ids = _unique_uuids(payload.get("issue_ids"), "issue_ids")
    issues = list(Issue.issue_objects.filter(workspace=workspace, project=project, id__in=issue_ids))
    if len(issues) != len(issue_ids):
        raise ActionValidationError("One or more work items were not found")
    fields = set(payload.get("fields_to_update") or [])
    if not fields or not fields.issubset(BULK_UPDATE_FIELDS):
        raise ActionValidationError("At least one field must be updated")
    _validate_related_ids(project, payload, fields)
    for issue in issues:
        _update_one_issue(workspace, user, project, issue, payload, fields)
    return {
        "action_type": "bulk_update_issues",
        "issue_ids": [str(issue.id) for issue in issues],
        "updated_count": len(issues),
        "project_id": str(project.id),
    }


def _archive_issues(workspace, user, payload):
    project = _project_for_action(workspace, user, payload.get("project_id"))
    issue_ids = _unique_uuids(payload.get("issue_ids"), "issue_ids")
    issues = list(
        Issue.issue_objects.filter(workspace=workspace, project=project, id__in=issue_ids).select_related("state")
    )
    if len(issues) != len(issue_ids):
        raise ActionValidationError("One or more work items were not found")
    if any(issue.state.group not in {"completed", "cancelled"} for issue in issues):
        raise ActionValidationError("Only completed or cancelled work items can be archived")
    now = timezone.now()
    archived_at = now.date()
    for issue in issues:
        current_instance = json.dumps(IssueSerializer(issue).data, cls=DjangoJSONEncoder)
        issue.archived_at = archived_at
        issue.updated_at = now
        _schedule_activity(
            type="issue.activity.updated",
            requested_data=json.dumps({"archived_at": str(archived_at), "automation": False}),
            actor_id=str(user.id),
            issue_id=str(issue.id),
            project_id=str(project.id),
            current_instance=current_instance,
            epoch=int(timezone.now().timestamp()),
            notification=True,
        )
    Issue.objects.bulk_update(issues, ["archived_at", "updated_at"])
    return {
        "action_type": "archive_issues",
        "issue_ids": [str(issue.id) for issue in issues],
        "archived_count": len(issues),
        "project_id": str(project.id),
    }


ACTION_EXECUTORS = {
    "create_comment": _create_comment,
    "edit_issue_description": _edit_issue_description,
    "create_subtask": _create_subtask,
    "create_issue": _create_issue,
    "update_issue": _update_issue,
    "create_module": _create_module,
    "create_cycle": _create_cycle,
    "bulk_update_issues": _bulk_update_issues,
    "archive_issues": _archive_issues,
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
