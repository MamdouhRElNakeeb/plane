from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, Count, Exists, F, FilteredRelation, IntegerField, OuterRef, Prefetch, Q, When
from django.utils import timezone

from plane.crete_ai.context import _bound_items, _issue_context, _visible_issue_filter
from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueType,
    Label,
    Module,
    ModuleIssue,
    Project,
    State,
    User,
)

REPORT_ACTION_HINTS = (
    "report",
    "list",
    "show",
    "count",
    "how many",
    "group",
    "breakdown",
    "summary",
    "summarize",
    "analyze",
    "compare",
)
REPORT_ANALYTIC_HINTS = (
    "overdue",
    "due today",
    "unresolved",
    "completed",
    "status",
    "priority",
    "assignee",
    "cycle",
    "sprint",
    "module",
    "label",
    "workload",
    "progress",
)
REPORT_ENTITY_HINTS = ("work item", "work items", "issue", "issues", "tasks", "project", "cycle", "sprint")
STATE_GROUPS = {"backlog", "unstarted", "started", "completed", "cancelled"}
PRIORITIES = {"urgent", "high", "medium", "low", "none"}
GROUPS = {
    "none",
    "state",
    "state_group",
    "assignee",
    "priority",
    "project",
    "cycle",
    "module",
    "label",
    "issue_type",
}
SORTS = {"updated_desc", "created_desc", "due_asc", "priority_desc"}
DUE_FILTERS = {"any", "overdue", "due_today", "no_due_date"}
FILTER_KINDS = {
    "project_ids": "project",
    "state_ids": "state",
    "cycle_ids": "cycle",
    "module_ids": "module",
    "label_ids": "label",
    "issue_type_ids": "issue_type",
    "assignee_ids": "assignee",
}


class ReportValidationError(Exception):
    pass


def should_plan_report(prompt):
    normalized = prompt.casefold()
    return any(hint in normalized for hint in REPORT_ACTION_HINTS) or (
        any(hint in normalized for hint in REPORT_ANALYTIC_HINTS)
        and any(hint in normalized for hint in REPORT_ENTITY_HINTS)
    )


def _catalog_item(instance, kind, **values):
    return {
        "id": str(instance.id),
        "kind": kind,
        "name": instance.name,
        **values,
    }


def _merge_catalog_groups(groups, limit):
    merged = []
    index = 0
    while len(merged) < limit:
        added = False
        for group in groups:
            if index < len(group):
                merged.append(group[index])
                added = True
                if len(merged) == limit:
                    break
        if not added:
            break
        index += 1
    return merged


def build_report_catalog(
    workspace,
    user,
    allowed_project_ids,
    restricted_project_ids,
    context_project_id=None,
):
    scope_project_ids = list(allowed_project_ids)
    if context_project_id is not None:
        if str(context_project_id) not in {str(value) for value in scope_project_ids}:
            raise ReportValidationError("Project is outside the report scope")
        scope_project_ids = [context_project_id]

    visible_issues = Issue.issue_objects.filter(
        workspace=workspace,
        project_id__in=scope_project_ids,
    ).filter(_visible_issue_filter(scope_project_ids, restricted_project_ids, user))
    now = timezone.now()
    per_kind_limit = settings.CRETE_AI_REPORT_CATALOG_KIND_LIMIT
    projects = list(
        Project.objects.filter(
            id__in=scope_project_ids,
            archived_at__isnull=True,
            deleted_at__isnull=True,
        ).order_by("name")[:per_kind_limit]
    )
    project_items = [_catalog_item(project, "project", identifier=project.identifier) for project in projects]
    states = list(
        State.objects.filter(
            id__in=visible_issues.values("state_id"),
            is_triage=False,
            deleted_at__isnull=True,
        ).order_by("project_id", "sequence")[:per_kind_limit]
    )
    state_items = [
        _catalog_item(
            state,
            "state",
            project_id=str(state.project_id),
            group=state.group,
        )
        for state in states
    ]
    cycles = list(
        Cycle.objects.filter(
            issue_cycle__issue__in=visible_issues,
            issue_cycle__deleted_at__isnull=True,
            archived_at__isnull=True,
            deleted_at__isnull=True,
        )
        .distinct()
        .order_by("-end_date", "name")[:per_kind_limit]
    )
    cycle_items = [
        _catalog_item(
            cycle,
            "cycle",
            project_id=str(cycle.project_id),
            is_current=bool(cycle.start_date and cycle.end_date and cycle.start_date <= now <= cycle.end_date),
        )
        for cycle in cycles
    ]
    modules = list(
        Module.objects.filter(
            issue_module__issue__in=visible_issues,
            issue_module__deleted_at__isnull=True,
            archived_at__isnull=True,
            deleted_at__isnull=True,
        )
        .distinct()
        .order_by("name")[:per_kind_limit]
    )
    module_items = [_catalog_item(module, "module", project_id=str(module.project_id)) for module in modules]
    labels = list(
        Label.objects.filter(
            workspace=workspace,
            deleted_at__isnull=True,
            label_issue__issue__in=visible_issues,
            label_issue__deleted_at__isnull=True,
        )
        .distinct()
        .order_by("name")[:per_kind_limit]
    )
    label_items = [
        _catalog_item(
            label,
            "label",
            **({"project_id": str(label.project_id)} if label.project_id else {}),
        )
        for label in labels
    ]
    issue_types = list(
        IssueType.objects.filter(
            workspace=workspace,
            is_active=True,
            deleted_at__isnull=True,
            issue_type__in=visible_issues,
        )
        .distinct()
        .order_by("name")[:per_kind_limit]
    )
    issue_type_items = [_catalog_item(issue_type, "issue_type") for issue_type in issue_types]

    members = list(
        User.objects.filter(
            is_active=True,
            issue_assignee__issue__in=visible_issues,
            issue_assignee__deleted_at__isnull=True,
            member_project__project_id__in=scope_project_ids,
            member_project__project_id=F("issue_assignee__issue__project_id"),
            member_project__is_active=True,
            member_project__deleted_at__isnull=True,
        )
        .distinct()
        .order_by("display_name", "email")[:per_kind_limit]
    )
    assignee_items = [
        {
            "id": str(member.id),
            "kind": "assignee",
            "name": member.display_name or member.email or member.username,
        }
        for member in members
    ]

    items = _merge_catalog_groups(
        [
            project_items,
            state_items,
            cycle_items,
            module_items,
            label_items,
            issue_type_items,
            assignee_items,
        ],
        settings.CRETE_AI_REPORT_CATALOG_LIMIT,
    )
    return items, scope_project_ids


def _strict_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ReportValidationError(f"Invalid {label}")


def _uuid_filter(filters, key, catalog_by_kind):
    values = filters.get(key, [])
    if not isinstance(values, list) or len(values) > 20:
        raise ReportValidationError(f"Invalid {key}")
    try:
        normalized = [str(UUID(str(value))) for value in values]
    except (TypeError, ValueError) as error:
        raise ReportValidationError(f"Invalid {key}") from error
    if not set(normalized).issubset(catalog_by_kind[FILTER_KINDS[key]]):
        raise ReportValidationError(f"Unknown {key}")
    return normalized


def validate_report_plan(plan, catalog):
    plan_keys = {"mode", "filters", "group_by", "include_items", "sort", "limit"}
    filter_keys = {
        *FILTER_KINDS,
        "state_groups",
        "priorities",
        "current_cycle",
        "due",
        "name_contains",
        "created_after",
        "created_before",
        "updated_after",
    }
    _strict_keys(plan, plan_keys, "report plan")
    if plan.get("mode") != "report":
        raise ReportValidationError("Not a report plan")
    filters = plan.get("filters", {})
    _strict_keys(filters, filter_keys, "report filters")
    catalog_by_kind = {
        kind: {str(item["id"]) for item in catalog if item.get("kind") == kind} for kind in set(FILTER_KINDS.values())
    }
    normalized_filters = {key: _uuid_filter(filters, key, catalog_by_kind) for key in FILTER_KINDS}
    state_groups = filters.get("state_groups", [])
    priorities = filters.get("priorities", [])
    if not isinstance(state_groups, list) or len(state_groups) > 5 or not set(state_groups).issubset(STATE_GROUPS):
        raise ReportValidationError("Invalid state groups")
    if not isinstance(priorities, list) or len(priorities) > 5 or not set(priorities).issubset(PRIORITIES):
        raise ReportValidationError("Invalid priorities")
    normalized_filters["state_groups"] = state_groups
    normalized_filters["priorities"] = priorities

    current_cycle = filters.get("current_cycle", False)
    due = filters.get("due", "any")
    name_contains = filters.get("name_contains")
    if not isinstance(current_cycle, bool) or due not in DUE_FILTERS:
        raise ReportValidationError("Invalid relative filters")
    if name_contains is not None and (
        not isinstance(name_contains, str) or not name_contains.strip() or len(name_contains) > 200
    ):
        raise ReportValidationError("Invalid name filter")
    normalized_filters["current_cycle"] = current_cycle
    normalized_filters["due"] = due
    normalized_filters["name_contains"] = name_contains.strip() if name_contains else None
    for key in ("created_after", "created_before", "updated_after"):
        value = filters.get(key)
        if value is None:
            normalized_filters[key] = None
            continue
        try:
            normalized_filters[key] = date.fromisoformat(str(value))
        except ValueError as error:
            raise ReportValidationError(f"Invalid {key}") from error

    group_by = plan.get("group_by", "none")
    sort = plan.get("sort", "updated_desc")
    include_items = plan.get("include_items", True)
    limit = plan.get("limit", 20)
    if group_by not in GROUPS or sort not in SORTS:
        raise ReportValidationError("Invalid report presentation")
    if (
        not isinstance(include_items, bool)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 25
    ):
        raise ReportValidationError("Invalid report limit")
    return {
        "filters": normalized_filters,
        "group_by": group_by,
        "include_items": include_items,
        "sort": sort,
        "limit": limit,
    }


def _apply_report_filters(queryset, filters, workspace):
    direct_filters = {
        "project_id__in": filters["project_ids"],
        "state_id__in": filters["state_ids"],
        "state__group__in": filters["state_groups"],
        "priority__in": filters["priorities"],
        "type_id__in": filters["issue_type_ids"],
    }
    for lookup, values in direct_filters.items():
        if values:
            queryset = queryset.filter(**{lookup: values})
    if filters["assignee_ids"]:
        queryset = queryset.filter(
            issue_assignee__assignee_id__in=filters["assignee_ids"],
            issue_assignee__deleted_at__isnull=True,
            issue_assignee__assignee__is_active=True,
            issue_assignee__assignee__member_project__project_id=F("project_id"),
            issue_assignee__assignee__member_project__is_active=True,
            issue_assignee__assignee__member_project__deleted_at__isnull=True,
        )
    relation_filters = {
        "issue_cycle__cycle_id__in": ("cycle_ids", "issue_cycle", "cycle"),
        "issue_module__module_id__in": ("module_ids", "issue_module", "module"),
        "label_issue__label_id__in": ("label_ids", "label_issue", "label"),
    }
    for lookup, (key, relation, target) in relation_filters.items():
        if filters[key]:
            relation_filter = {
                lookup: filters[key],
                f"{relation}__deleted_at__isnull": True,
            }
            if target:
                relation_filter[f"{relation}__{target}__deleted_at__isnull"] = True
            queryset = queryset.filter(
                **relation_filter,
            )
    if filters["current_cycle"]:
        now = timezone.now()
        queryset = queryset.filter(
            issue_cycle__deleted_at__isnull=True,
            issue_cycle__cycle__deleted_at__isnull=True,
            issue_cycle__cycle__archived_at__isnull=True,
            issue_cycle__cycle__start_date__lte=now,
            issue_cycle__cycle__end_date__gte=now,
        )
    workspace_timezone = ZoneInfo(workspace.timezone)
    today = datetime.now(workspace_timezone).date()
    if filters["due"] == "overdue":
        queryset = queryset.filter(target_date__lt=today).exclude(state__group__in=["completed", "cancelled"])
    elif filters["due"] == "due_today":
        queryset = queryset.filter(target_date=today)
    elif filters["due"] == "no_due_date":
        queryset = queryset.filter(target_date__isnull=True)
    if filters["name_contains"]:
        queryset = queryset.filter(name__icontains=filters["name_contains"])
    if filters["created_after"]:
        created_after = datetime.combine(filters["created_after"], time.min, tzinfo=workspace_timezone)
        queryset = queryset.filter(created_at__gte=created_after)
    if filters["created_before"]:
        created_before = datetime.combine(
            filters["created_before"] + timedelta(days=1),
            time.min,
            tzinfo=workspace_timezone,
        )
        queryset = queryset.filter(created_at__lt=created_before)
    if filters["updated_after"]:
        updated_after = datetime.combine(filters["updated_after"], time.min, tzinfo=workspace_timezone)
        queryset = queryset.filter(updated_at__gte=updated_after)
    return queryset.distinct()


def _group_report(queryset, group_by):
    if group_by == "none":
        return []
    direct_groups = {
        "state": (["state_id", "state__name"], "No status"),
        "state_group": (["state__group"], "No status group"),
        "priority": (["priority"], "No priority"),
        "project": (["project_id", "project__name"], "No project"),
        "issue_type": (["type_id", "type__name"], "No issue type"),
    }
    if group_by in direct_groups:
        fields, empty_label = direct_groups[group_by]
        values = queryset.values(*fields).annotate(count=Count("id", distinct=True)).order_by("-count")[:50]
        return [
            {
                "id": str(row.get(fields[0])) if row.get(fields[0]) is not None else None,
                "name": row.get(fields[-1]) or empty_label,
                "count": row["count"],
            }
            for row in values
        ]

    if group_by == "assignee":
        valid_assignments = IssueAssignee.objects.filter(
            issue__in=queryset,
            deleted_at__isnull=True,
            assignee__is_active=True,
            assignee__member_project__project_id=F("project_id"),
            assignee__member_project__is_active=True,
            assignee__member_project__deleted_at__isnull=True,
        )
        values = (
            valid_assignments.values(
                "assignee_id",
                "assignee__display_name",
                "assignee__email",
            )
            .annotate(count=Count("issue_id", distinct=True))
            .order_by("-count")[:49]
        )
        groups = [
            {
                "id": str(row["assignee_id"]),
                "name": row["assignee__display_name"] or row["assignee__email"],
                "count": row["count"],
            }
            for row in values
        ]
        valid_assignment_for_issue = IssueAssignee.objects.filter(
            issue_id=OuterRef("pk"),
            deleted_at__isnull=True,
            assignee__is_active=True,
            assignee__member_project__project_id=OuterRef("project_id"),
            assignee__member_project__is_active=True,
            assignee__member_project__deleted_at__isnull=True,
        )
        unassigned_count = (
            queryset.annotate(has_valid_assignee=Exists(valid_assignment_for_issue))
            .filter(has_valid_assignee=False)
            .values("id")
            .distinct()
            .count()
        )
        if unassigned_count:
            groups.append(
                {
                    "id": None,
                    "name": "Unassigned",
                    "count": unassigned_count,
                }
            )
        return groups

    relation_groups = {
        "cycle": (
            "active_cycle",
            "issue_cycle",
            Q(issue_cycle__deleted_at__isnull=True),
            "active_cycle_target",
            "active_cycle__cycle",
            Q(
                active_cycle__cycle__deleted_at__isnull=True,
                active_cycle__cycle__archived_at__isnull=True,
            ),
            ["active_cycle_target__id", "active_cycle_target__name"],
            "No cycle",
        ),
        "module": (
            "active_module",
            "issue_module",
            Q(issue_module__deleted_at__isnull=True),
            "active_module_target",
            "active_module__module",
            Q(
                active_module__module__deleted_at__isnull=True,
                active_module__module__archived_at__isnull=True,
            ),
            ["active_module_target__id", "active_module_target__name"],
            "No module",
        ),
        "label": (
            "active_label",
            "label_issue",
            Q(label_issue__deleted_at__isnull=True),
            "active_label_target",
            "active_label__label",
            Q(active_label__label__deleted_at__isnull=True),
            ["active_label_target__id", "active_label_target__name"],
            "No label",
        ),
    }
    alias, relation, condition, target_alias, target_relation, target_condition, fields, empty_label = relation_groups[
        group_by
    ]
    values = (
        queryset.annotate(**{alias: FilteredRelation(relation, condition=condition)})
        .annotate(**{target_alias: FilteredRelation(target_relation, condition=target_condition)})
        .values(*fields)
        .annotate(count=Count("id", distinct=True))
        .order_by("-count")[:50]
    )
    return [
        {
            "id": str(row.get(fields[0])) if row.get(fields[0]) is not None else None,
            "name": next((row.get(field) for field in fields[1:] if row.get(field)), empty_label),
            "count": row["count"],
        }
        for row in values
    ]


def _ordered_report_queryset(queryset, sort):
    if sort == "created_desc":
        return queryset.order_by("-created_at", "id")
    if sort == "due_asc":
        return queryset.order_by(F("target_date").asc(nulls_last=True), "id")
    if sort == "priority_desc":
        return queryset.annotate(
            report_priority_order=Case(
                When(priority="urgent", then=0),
                When(priority="high", then=1),
                When(priority="medium", then=2),
                When(priority="low", then=3),
                default=4,
                output_field=IntegerField(),
            )
        ).order_by("report_priority_order", "-updated_at", "id")
    return queryset.order_by("-updated_at", "id")


def _report_issue_row(issue):
    return {
        "object_id": str(issue.id),
        "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
        "title": issue.name,
        "project": issue.project.name,
        "status": issue.state.name if issue.state_id else None,
        "state_group": issue.state.group if issue.state_id else None,
        "priority": issue.priority,
        "start_date": issue.start_date.isoformat() if issue.start_date else None,
        "due_date": issue.target_date.isoformat() if issue.target_date else None,
        "issue_type": issue.type.name if issue.type_id else None,
        "assignees": [
            relation.assignee.display_name or relation.assignee.email or relation.assignee.username
            for relation in issue.report_assignees
        ],
        "cycles": [relation.cycle.name for relation in issue.report_cycles],
        "modules": [relation.module.name for relation in issue.report_modules],
        "labels": [relation.label.name for relation in issue.report_labels],
        "updated_at": issue.updated_at.isoformat(),
    }


def execute_report(
    *,
    plan,
    catalog,
    workspace,
    user,
    allowed_project_ids,
    restricted_project_ids,
    context_project_id=None,
):
    validated = validate_report_plan(plan, catalog)
    scope_project_ids = list(allowed_project_ids)
    if context_project_id is not None:
        scope_project_ids = [context_project_id]
    queryset = (
        Issue.issue_objects.select_related("project", "state", "type")
        .filter(
            workspace=workspace,
            project_id__in=scope_project_ids,
        )
        .filter(_visible_issue_filter(scope_project_ids, restricted_project_ids, user))
    )
    queryset = _apply_report_filters(queryset, validated["filters"], workspace)
    total = queryset.values("id").distinct().count()
    groups = _group_report(queryset, validated["group_by"])
    items = []
    context_items = []
    if validated["include_items"]:
        item_queryset = _ordered_report_queryset(queryset, validated["sort"]).prefetch_related(
            Prefetch(
                "issue_assignee",
                queryset=IssueAssignee.objects.filter(
                    deleted_at__isnull=True,
                    assignee__is_active=True,
                    assignee__member_project__project_id=F("project_id"),
                    assignee__member_project__is_active=True,
                    assignee__member_project__deleted_at__isnull=True,
                )
                .distinct()
                .select_related("assignee"),
                to_attr="report_assignees",
            ),
            Prefetch(
                "issue_cycle",
                queryset=CycleIssue.objects.filter(
                    deleted_at__isnull=True,
                    cycle__deleted_at__isnull=True,
                    cycle__archived_at__isnull=True,
                ).select_related("cycle"),
                to_attr="report_cycles",
            ),
            Prefetch(
                "issue_module",
                queryset=ModuleIssue.objects.filter(
                    deleted_at__isnull=True,
                    module__deleted_at__isnull=True,
                    module__archived_at__isnull=True,
                ).select_related("module"),
                to_attr="report_modules",
            ),
            Prefetch(
                "label_issue",
                queryset=IssueLabel.objects.filter(
                    deleted_at__isnull=True,
                    label__deleted_at__isnull=True,
                ).select_related("label"),
                to_attr="report_labels",
            ),
        )[: validated["limit"]]
        issues = list(item_queryset)
        items = [_report_issue_row(issue) for issue in issues]
        context_items = _bound_items([_issue_context(issue) for issue in issues])
    report = {
        "total": total,
        "groups": groups,
        "items": items,
        "items_returned": len(items),
        "items_truncated": total > len(items),
        "applied_filters": {
            key: (
                [str(item) for item in value]
                if isinstance(value, list)
                else str(value)
                if isinstance(value, date)
                else value
            )
            for key, value in validated["filters"].items()
            if value not in (None, [], False, "any")
        },
        "group_by": validated["group_by"],
        "generated_at": timezone.now().isoformat(),
    }
    return report, context_items
