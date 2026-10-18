from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.test import override_settings

from plane.crete_ai.actions import (
    ACTION_EXECUTORS,
    ActionValidationError,
    _action_data,
    _archive_issues,
    _create_comment,
    _create_cycle,
    _create_issue,
    _create_module,
    _create_subtask,
    _edit_issue_description,
    _project_for_action,
    _sanitize_html,
    _update_issue,
    execute_confirmed_action,
)


pytestmark = pytest.mark.unit


def test_azure_description_tool_name_maps_to_confirmed_action():
    action_type, payload = _action_data(
        {
            "action_name": "update_description",
            "arguments": {"description_html": "<p>Updated</p>"},
        }
    )

    assert action_type == "edit_issue_description"
    assert payload == {"description_html": "<p>Updated</p>"}


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions._schedule_activity")
@patch("plane.crete_ai.actions._sanitize_html", return_value="<p>Hello</p>")
@patch("plane.crete_ai.actions.IssueCommentCreateSerializer")
@patch("plane.crete_ai.actions._issue_for_action")
@patch("plane.crete_ai.actions._project_for_action")
def test_confirmed_comment_uses_plane_comment_serializer(
    project_check,
    issue_check,
    serializer_class,
    _sanitize,
    _activity,
    _model_activity,
):
    project = SimpleNamespace(id=uuid4())
    issue = SimpleNamespace(id=uuid4())
    comment = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    issue_check.return_value = issue
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True
    serializer.save.return_value = comment
    serializer.data = {"comment_html": "<p>Hello</p>"}

    result = _create_comment(
        workspace,
        user,
        {
            "project_id": str(project.id),
            "issue_id": str(issue.id),
            "comment_html": "<p>Hello</p>",
        },
    )

    serializer.save.assert_called_once_with(project=project, issue=issue, actor=user)
    assert result["comment_id"] == str(comment.id)


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions._schedule_activity")
@patch("plane.crete_ai.actions._sanitize_html", return_value="<p>Updated</p>")
@patch("plane.crete_ai.actions.IssueSerializer")
@patch("plane.crete_ai.actions._issue_for_action")
@patch("plane.crete_ai.actions._project_for_action")
def test_confirmed_description_edit_uses_partial_issue_update(
    project_check,
    issue_check,
    serializer_class,
    _sanitize,
    _activity,
    _model_activity,
):
    project = SimpleNamespace(id=uuid4())
    issue = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    issue_check.return_value = issue
    serializer_class.return_value.data = {}
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True

    _edit_issue_description(
        workspace,
        user,
        {
            "project_id": str(project.id),
            "issue_id": str(issue.id),
            "description_html": "<p>Updated</p>",
        },
    )

    serializer_class.assert_any_call(
        issue,
        data={"description_html": "<p>Updated</p>"},
        context={"project_id": project.id, "workspace_id": workspace.id},
        partial=True,
    )
    serializer.save.assert_called_once()


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions._schedule_activity")
@patch("plane.crete_ai.actions._sanitize_html", return_value="")
@patch("plane.crete_ai.actions.IssueSerializer")
@patch("plane.crete_ai.actions._issue_for_action")
@patch("plane.crete_ai.actions._project_for_action")
def test_confirmed_subtask_uses_project_defaults(
    project_check,
    issue_check,
    serializer_class,
    _sanitize,
    _activity,
    _model_activity,
):
    project = SimpleNamespace(id=uuid4(), default_assignee_id=uuid4())
    parent = SimpleNamespace(id=uuid4())
    issue = SimpleNamespace(id=uuid4(), name="Child")
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    issue_check.return_value = parent
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True
    serializer.save.return_value = issue

    result = _create_subtask(
        workspace,
        user,
        {
            "project_id": str(project.id),
            "parent_issue_id": str(parent.id),
            "name": "Child",
            "description_html": "",
        },
    )

    assert serializer_class.call_args.kwargs["context"] == {
        "project_id": project.id,
        "workspace_id": workspace.id,
        "default_assignee_id": project.default_assignee_id,
    }
    assert result["issue_id"] == str(issue.id)


@patch("plane.crete_ai.actions.ProjectMember.objects")
def test_guest_or_inactive_member_cannot_confirm_action(member_manager):
    member_manager.select_related.return_value.filter.return_value.first.return_value = None

    with pytest.raises(ActionValidationError, match="not permitted"):
        _project_for_action(
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4()),
            uuid4(),
        )

    filters = member_manager.select_related.return_value.filter.call_args.kwargs
    assert filters["role__in"] == [20, 15]
    assert filters["is_active"] is True


@override_settings(CRETE_AI_ACTION_HTML_CHARS=10)
def test_action_html_has_a_strict_size_limit():
    with pytest.raises(ActionValidationError, match="too long"):
        _sanitize_html("<p>too much text</p>", "description_html")


@patch("plane.crete_ai.actions._project_for_action")
@patch("plane.crete_ai.actions.ConfirmedAction.objects")
@patch("plane.crete_ai.actions.transaction.atomic")
def test_completed_confirmation_is_idempotent_and_rechecks_membership(
    atomic,
    action_manager,
    project_check,
):
    workspace = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    project_id = uuid4()
    action_id = uuid4()
    result = {"comment_id": str(uuid4())}
    record = SimpleNamespace(
        workspace_id=workspace.id,
        user_id=user.id,
        action_type="create_comment",
        status="completed",
        result=result,
    )
    action_manager.select_for_update.return_value.get_or_create.return_value = (
        record,
        False,
    )
    atomic.return_value.__enter__.return_value = None
    executor = MagicMock()

    with patch.dict(ACTION_EXECUTORS, {"create_comment": executor}):
        returned = execute_confirmed_action(
            action_id,
            {
                "action_name": "create_comment",
                "payload": {
                    "project_id": str(project_id),
                    "issue_id": str(uuid4()),
                    "comment_html": "<p>hello</p>",
                },
            },
            workspace,
            user,
        )

    assert returned == result
    project_check.assert_called_once_with(workspace, user, str(project_id))
    executor.assert_not_called()


def test_action_cannot_cross_workspace_boundary():
    workspace = SimpleNamespace(id=uuid4())
    with pytest.raises(ActionValidationError, match="workspace"):
        execute_confirmed_action(
            uuid4(),
            {
                "type": "create_subtask",
                "workspace_id": str(uuid4()),
                "payload": {"project_id": str(uuid4())},
            },
            workspace,
            SimpleNamespace(id=uuid4()),
        )


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions._schedule_activity")
@patch("plane.crete_ai.actions._set_issue_relations")
@patch("plane.crete_ai.actions._validate_related_ids")
@patch("plane.crete_ai.actions.IssueSerializer")
@patch("plane.crete_ai.actions._project_for_action")
def test_create_issue_uses_default_state_and_project_defaults(
    project_check,
    serializer_class,
    _validate_related,
    set_relations,
    _activity,
    _model_activity,
):
    state_id = uuid4()
    project = SimpleNamespace(id=uuid4(), default_state_id=state_id, default_assignee_id=None)
    issue = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True
    serializer.save.return_value = issue

    result = _create_issue(
        workspace,
        user,
        {
            "project_id": str(project.id),
            "name": "Launch checklist",
            "state_id": None,
            "priority": "high",
            "assignee_ids": [],
            "label_ids": [],
            "module_ids": [],
        },
    )

    assert serializer_class.call_args.kwargs["data"]["state"] == str(state_id)
    serializer.save.assert_called_once_with(created_by=user, updated_by=user)
    set_relations.assert_called_once()
    assert result["action_type"] == "create_issue"


@patch("plane.crete_ai.actions._update_one_issue")
@patch("plane.crete_ai.actions._issue_for_action")
@patch("plane.crete_ai.actions._project_for_action")
def test_update_issue_applies_only_explicit_fields(project_check, issue_check, update_one):
    project = SimpleNamespace(id=uuid4())
    issue = SimpleNamespace(id=uuid4())
    project_check.return_value = project
    issue_check.return_value = issue

    result = _update_issue(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
        {
            "project_id": str(project.id),
            "issue_id": str(issue.id),
            "fields_to_update": ["priority"],
            "priority": "urgent",
            "target_date": None,
        },
    )

    assert update_one.call_args.args[-1] == {"priority"}
    assert result["action_type"] == "update_issue"


@patch("plane.crete_ai.actions._project_for_action")
def test_archive_rejects_non_completed_work_items(project_check):
    project = SimpleNamespace(id=uuid4())
    project_check.return_value = project
    issue = SimpleNamespace(id=uuid4(), state=SimpleNamespace(group="started"))
    queryset = MagicMock()
    queryset.select_related.return_value = [issue]

    with patch("plane.crete_ai.actions.Issue.issue_objects") as issue_manager:
        issue_manager.filter.return_value = queryset
        with pytest.raises(ActionValidationError, match="completed or cancelled"):
            _archive_issues(
                SimpleNamespace(id=uuid4()),
                SimpleNamespace(id=uuid4()),
                {"project_id": str(project.id), "issue_ids": [str(issue.id)]},
            )


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions.ModuleCreateSerializer")
@patch("plane.crete_ai.actions._project_for_action")
def test_create_module_uses_plane_serializer(project_check, serializer_class, _model_activity):
    project = SimpleNamespace(id=uuid4())
    module = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True
    serializer.save.return_value = module

    result = _create_module(
        workspace,
        user,
        {"project_id": str(project.id), "name": "Launch", "description": ""},
    )

    serializer.save.assert_called_once_with(created_by=user, updated_by=user)
    assert result["module_id"] == str(module.id)


@patch("plane.crete_ai.actions._schedule_model_activity")
@patch("plane.crete_ai.actions.CycleCreateSerializer")
@patch("plane.crete_ai.actions._project_for_action")
def test_create_cycle_normalizes_date_boundaries(project_check, serializer_class, _model_activity):
    project = SimpleNamespace(id=uuid4())
    cycle = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), slug="workspace")
    project_check.return_value = project
    serializer = serializer_class.return_value
    serializer.is_valid.return_value = True
    serializer.save.return_value = cycle

    result = _create_cycle(
        workspace,
        user,
        {
            "project_id": str(project.id),
            "name": "September",
            "description": "",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        },
    )

    data = serializer_class.call_args.kwargs["data"]
    assert data["start_date"] == "2026-09-01"
    assert data["end_date"] == "2026-09-30"
    assert result["cycle_id"] == str(cycle.id)
