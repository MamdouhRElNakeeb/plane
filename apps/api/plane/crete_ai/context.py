import json

from django.conf import settings
from django.db.models import Q

from plane.db.models import Issue, IssueComment, ProjectMember, WorkspaceMember


class ContextValidationError(Exception):
    pass


def get_workspace_for_user(slug, user):
    membership = (
        WorkspaceMember.objects.select_related("workspace")
        .filter(workspace__slug=slug, member=user, is_active=True)
        .first()
    )
    if membership is None:
        raise ContextValidationError("Workspace not found")
    return membership.workspace


def get_allowed_project_ids(workspace, user):
    return list(
        ProjectMember.objects.filter(
            workspace=workspace,
            member=user,
            is_active=True,
            project__archived_at__isnull=True,
        ).values_list("project_id", flat=True)
    )


def get_restricted_guest_project_ids(workspace, user):
    return set(
        ProjectMember.objects.filter(
            workspace=workspace,
            member=user,
            role=5,
            is_active=True,
            project__archived_at__isnull=True,
            project__guest_view_all_features=False,
        ).values_list("project_id", flat=True)
    )


def _visible_issue_filter(allowed_project_ids, restricted_project_ids, user):
    unrestricted_project_ids = [
        project_id for project_id in allowed_project_ids if project_id not in restricted_project_ids
    ]
    return Q(project_id__in=unrestricted_project_ids) | Q(
        project_id__in=restricted_project_ids,
        created_by=user,
    )


def validate_context(workspace, user, context_type, project_id=None, issue_id=None):
    if context_type not in {"general", "workspace", "project", "work_item"}:
        raise ContextValidationError("Invalid context type")

    allowed_project_ids = get_allowed_project_ids(workspace, user)
    allowed_project_id_set = {str(value) for value in allowed_project_ids}

    if context_type in {"project", "work_item"}:
        if project_id is None or str(project_id) not in allowed_project_id_set:
            raise ContextValidationError("Project not found")
    elif project_id is not None:
        raise ContextValidationError("Project is not valid for this context")

    issue = None
    if context_type == "work_item":
        if issue_id is None:
            raise ContextValidationError("Work item is required")
        restricted_project_ids = get_restricted_guest_project_ids(workspace, user)
        issue = (
            Issue.issue_objects.select_related("project", "state")
            .filter(
                id=issue_id,
                workspace=workspace,
                project_id=project_id,
                project_id__in=allowed_project_ids,
            )
            .filter(_visible_issue_filter(allowed_project_ids, restricted_project_ids, user))
            .first()
        )
        if issue is None:
            raise ContextValidationError("Work item not found")
    elif issue_id is not None:
        raise ContextValidationError("Work item is not valid for this context")

    restricted_project_ids = [project_id] if context_type in {"project", "work_item"} else allowed_project_ids
    return restricted_project_ids, issue


def _truncate(value, limit):
    value = (value or "").strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _issue_context(issue, include_comments=False):
    details = {
        "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
        "description": _truncate(
            issue.description_stripped,
            settings.CRETE_AI_CONTEXT_ITEM_CHARS,
        ),
        "priority": issue.priority,
        "status": (
            {"id": str(issue.state_id), "name": issue.state.name, "group": issue.state.group}
            if issue.state_id
            else None
        ),
        "project": {
            "id": str(issue.project_id),
            "name": issue.project.name,
            "identifier": issue.project.identifier,
        },
        "updated_at": issue.updated_at.isoformat(),
    }
    if include_comments:
        comments = (
            IssueComment.objects.filter(issue=issue)
            .select_related("actor")
            .order_by("-created_at")[: settings.CRETE_AI_CONTEXT_COMMENT_LIMIT]
        )
        details["comments"] = [
            {
                "id": str(comment.id),
                "content": _truncate(
                    comment.comment_stripped,
                    settings.CRETE_AI_CONTEXT_COMMENT_CHARS,
                ),
                "created_at": comment.created_at.isoformat(),
                "actor_id": str(comment.actor_id) if comment.actor_id else None,
            }
            for comment in comments
        ]
    return {
        "object_type": "issue",
        "object_id": str(issue.id),
        "project_id": str(issue.project_id),
        "title": _truncate(issue.name, settings.CRETE_AI_CONTEXT_ITEM_CHARS),
        "content": json.dumps(details, separators=(",", ":"), ensure_ascii=False),
    }


def extract_ranked_ids(response):
    if isinstance(response, list):
        values = response
    elif isinstance(response, dict):
        values = response.get(
            "ids",
            response.get(
                "issue_ids",
                response.get("results", response.get("matches", [])),
            ),
        )
    else:
        return []

    result = []
    for value in values:
        object_id = value.get("object_id") or value.get("id") if isinstance(value, dict) else value
        if object_id is not None:
            result.append(str(object_id))
    return result


def _bound_items(items):
    bounded = []
    remaining = settings.CRETE_AI_CONTEXT_TOTAL_CHARS
    for item in items[: settings.CRETE_AI_CONTEXT_ITEM_LIMIT]:
        if remaining <= 0:
            break
        item = dict(item)
        item["title"] = _truncate(item["title"], remaining)
        remaining -= len(item["title"])
        item["content"] = _truncate(item["content"], remaining)
        remaining -= len(item["content"])
        bounded.append(item)
    return bounded


def collect_chat_context(
    *,
    client,
    workspace,
    user,
    prompt,
    context_type,
    project_id=None,
    issue_id=None,
):
    allowed_project_ids, direct_issue = validate_context(
        workspace,
        user,
        context_type,
        project_id=project_id,
        issue_id=issue_id,
    )
    if context_type == "general":
        return [], allowed_project_ids

    retrieve_payload = {
        "workspace_id": str(workspace.id),
        "user_id": str(user.id),
        "query": prompt,
        "allowed_project_ids": [str(value) for value in allowed_project_ids],
        "limit": settings.CRETE_AI_RETRIEVAL_LIMIT,
    }
    if project_id is not None:
        retrieve_payload["project_id"] = str(project_id)

    ranked_ids = extract_ranked_ids(client.retrieve(retrieve_payload))
    restricted_project_ids = get_restricted_guest_project_ids(workspace, user)
    visible_issues = (
        Issue.issue_objects.select_related("project", "state")
        .filter(
            id__in=ranked_ids,
            workspace=workspace,
            project_id__in=allowed_project_ids,
        )
        .filter(_visible_issue_filter(allowed_project_ids, restricted_project_ids, user))
    )
    issues_by_id = {str(issue.id): issue for issue in visible_issues}

    issues = []
    seen = set()
    if direct_issue is not None:
        issues.append(_issue_context(direct_issue, include_comments=True))
        seen.add(str(direct_issue.id))
    for object_id in ranked_ids:
        issue = issues_by_id.get(object_id)
        if issue is not None and object_id not in seen:
            issues.append(_issue_context(issue))
            seen.add(object_id)
    return _bound_items(issues), allowed_project_ids


def issue_index_payload(issue):
    return {
        "object_type": "issue",
        "object_id": str(issue.id),
        "workspace_id": str(issue.workspace_id),
        "project_id": str(issue.project_id),
        "title": issue.name,
        "content": issue.description_stripped or "",
        "updated_at": issue.updated_at.isoformat(),
    }
