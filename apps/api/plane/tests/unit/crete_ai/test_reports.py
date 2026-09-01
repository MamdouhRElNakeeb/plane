from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.db.models import Q

from plane.crete_ai.reports import (
    ReportValidationError,
    _apply_report_filters,
    _group_report,
    _merge_catalog_groups,
    execute_report,
    should_plan_report,
    validate_report_plan,
)


pytestmark = pytest.mark.unit


def _plan(project_id, **filter_overrides):
    filters = {
        "project_ids": [str(project_id)],
        "state_groups": ["backlog", "unstarted", "started"],
        "state_ids": [],
        "priorities": [],
        "cycle_ids": [],
        "module_ids": [],
        "label_ids": [],
        "issue_type_ids": [],
        "assignee_ids": [],
        "current_cycle": False,
        "due": "any",
        "name_contains": None,
        "created_after": None,
        "created_before": None,
        "updated_after": None,
        **filter_overrides,
    }
    return {
        "mode": "report",
        "filters": filters,
        "group_by": "state",
        "include_items": False,
        "sort": "updated_desc",
        "limit": 20,
    }


def test_report_detection_is_conservative():
    assert should_plan_report("Count unresolved work by status")
    assert should_plan_report("Show overdue high-priority work")
    assert should_plan_report("Archive all completed work items")
    assert should_plan_report("Bulk update these tasks")
    assert should_plan_report("Assign these issues to Alice")
    assert not should_plan_report("Draft a comment explaining the status")
    assert not should_plan_report("Draft a small comment")


def test_catalog_limit_is_shared_fairly_across_entity_kinds():
    groups = [
        [{"kind": kind, "id": f"{kind}-{index}"} for index in range(100)]
        for kind in (
            "project",
            "state",
            "cycle",
            "module",
            "label",
            "issue_type",
            "assignee",
        )
    ]

    merged = _merge_catalog_groups(groups, 500)

    counts = {kind: sum(item["kind"] == kind for item in merged) for kind in {item["kind"] for item in merged}}
    assert len(merged) == 500
    assert set(counts) == {
        "project",
        "state",
        "cycle",
        "module",
        "label",
        "issue_type",
        "assignee",
    }
    assert max(counts.values()) - min(counts.values()) <= 1


def test_report_plan_rejects_unknown_catalog_ids_and_extra_fields():
    project_id = uuid4()
    catalog = [{"id": str(project_id), "kind": "project", "name": "Engineering"}]

    validated = validate_report_plan(_plan(project_id), catalog)

    assert validated["filters"]["project_ids"] == [str(project_id)]
    with pytest.raises(ReportValidationError, match="Unknown project_ids"):
        validate_report_plan(_plan(uuid4()), catalog)
    invalid = _plan(project_id)
    invalid["filters"]["raw_lookup"] = "project__workspace"
    with pytest.raises(ReportValidationError, match="Invalid report filters"):
        validate_report_plan(invalid, catalog)


def test_current_cycle_is_queried_directly_without_catalog_truncation():
    queryset = MagicMock()
    filters = validate_report_plan(
        _plan(uuid4(), project_ids=[], state_groups=[], current_cycle=True),
        [],
    )["filters"]

    result = _apply_report_filters(
        queryset,
        filters,
        workspace=SimpleNamespace(timezone="UTC"),
    )

    assert result is queryset.filter.return_value.distinct.return_value
    current_cycle_filter = queryset.filter.call_args.kwargs
    assert current_cycle_filter["issue_cycle__cycle__start_date__lte"]
    assert current_cycle_filter["issue_cycle__cycle__end_date__gte"]
    assert current_cycle_filter["issue_cycle__cycle__deleted_at__isnull"] is True


def test_report_date_filters_use_explicit_workspace_timezone_boundaries():
    project_id = uuid4()
    queryset = MagicMock()
    filtered = queryset.filter.return_value
    filtered.filter.return_value = filtered
    filtered.distinct.return_value = filtered
    filters = validate_report_plan(
        _plan(
            project_id,
            project_ids=[],
            state_groups=[],
            created_after="2026-07-01",
            created_before="2026-07-24",
        ),
        [],
    )["filters"]

    _apply_report_filters(
        queryset,
        filters,
        workspace=SimpleNamespace(timezone="Asia/Dubai"),
    )

    created_after = queryset.filter.call_args.kwargs["created_at__gte"]
    created_before = filtered.filter.call_args.kwargs["created_at__lt"]
    assert created_after.isoformat() == "2026-07-01T00:00:00+04:00"
    assert created_before.isoformat() == "2026-07-25T00:00:00+04:00"


def test_assignee_filters_require_active_same_project_membership():
    project_id = uuid4()
    assignee_id = uuid4()
    queryset = MagicMock()
    filters = validate_report_plan(
        _plan(
            project_id,
            project_ids=[],
            state_groups=[],
            assignee_ids=[str(assignee_id)],
        ),
        [{"id": str(assignee_id), "kind": "assignee", "name": "Member"}],
    )["filters"]

    _apply_report_filters(
        queryset,
        filters,
        workspace=SimpleNamespace(timezone="UTC"),
    )

    assignee_filter = queryset.filter.call_args.kwargs
    assert assignee_filter["issue_assignee__assignee_id__in"] == [str(assignee_id)]
    assert assignee_filter["issue_assignee__assignee__is_active"] is True
    assert assignee_filter["issue_assignee__assignee__member_project__is_active"] is True
    assert assignee_filter["issue_assignee__assignee__member_project__deleted_at__isnull"] is True
    assert assignee_filter["issue_assignee__assignee__member_project__project_id"].name == "project_id"


@patch("plane.crete_ai.reports.IssueAssignee.objects")
def test_assignee_groups_count_only_valid_assignments_and_separate_unassigned(
    issue_assignees,
):
    assignee_id = uuid4()
    queryset = MagicMock()
    values = issue_assignees.filter.return_value.values.return_value
    values.annotate.return_value.order_by.return_value.__getitem__.return_value = [
        {
            "assignee_id": assignee_id,
            "assignee__display_name": "Active Member",
            "assignee__email": "member@example.com",
            "count": 2,
        }
    ]
    queryset.annotate.return_value.filter.return_value.values.return_value.distinct.return_value.count.return_value = 1

    groups = _group_report(queryset, "assignee")

    valid_assignment_filter = issue_assignees.filter.call_args_list[0].kwargs
    assert valid_assignment_filter["assignee__member_project__is_active"] is True
    assert valid_assignment_filter["assignee__member_project__deleted_at__isnull"] is True
    assert valid_assignment_filter["assignee__member_project__project_id"].name == "project_id"
    assert groups == [
        {"id": str(assignee_id), "name": "Active Member", "count": 2},
        {"id": None, "name": "Unassigned", "count": 1},
    ]


@patch("plane.crete_ai.reports._visible_issue_filter")
@patch("plane.crete_ai.reports.Issue.issue_objects")
def test_report_totals_start_from_permission_scoped_issues(issue_manager, visible_issue_filter):
    project_id = uuid4()
    user = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), timezone="UTC")
    visibility = Q(created_by=user)
    visible_issue_filter.return_value = visibility
    filtered_queryset = MagicMock()
    report_queryset = MagicMock()
    issue_manager.select_related.return_value.filter.return_value.filter.return_value = filtered_queryset
    filtered_queryset.filter.return_value = filtered_queryset
    filtered_queryset.distinct.return_value = report_queryset
    report_queryset.values.return_value.distinct.return_value.count.return_value = 3
    catalog = [{"id": str(project_id), "kind": "project", "name": "Engineering"}]

    result, context_items = execute_report(
        plan={**_plan(project_id), "group_by": "none"},
        catalog=catalog,
        workspace=workspace,
        user=user,
        allowed_project_ids={str(project_id)},
        restricted_project_ids={str(project_id)},
    )

    assert result["total"] == 3
    assert result["items"] == []
    assert context_items == []
    base_filter = issue_manager.select_related.return_value.filter
    assert base_filter.call_args_list[0].kwargs == {
        "workspace": workspace,
        "project_id__in": [str(project_id)],
    }
    base_filter.return_value.filter.assert_called_once_with(visibility)
    visible_issue_filter.assert_called_once_with(
        [str(project_id)],
        {str(project_id)},
        user,
    )
