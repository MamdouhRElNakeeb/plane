import json
from uuid import UUID

from django.conf import settings
from django.db.models import Q

from plane.db.models import Issue, IssueComment, ProjectMember, WorkspaceMember

_MAX_CITATION_ID = 999_999_999


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


def _bound_context_content(value, limit):
    value = value or ""
    if len(value) <= limit:
        return value
    try:
        details = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return _truncate(value, limit)
    if not isinstance(details, dict) or not isinstance(details.get("project"), dict):
        return _truncate(value, limit)
    identifier = details.get("identifier")
    project_identifier = details["project"].get("identifier")
    if not isinstance(identifier, str) or not isinstance(project_identifier, str):
        return _truncate(value, limit)

    bounded = {
        "identifier": identifier,
        "project": {"identifier": project_identifier},
        "truncated_context": "",
    }

    def encode_bounded():
        return json.dumps(bounded, separators=(",", ":"), ensure_ascii=False)

    if len(encode_bounded()) > limit:
        return None

    low = 0
    high = len(value)
    while low < high:
        middle = (low + high + 1) // 2
        bounded["truncated_context"] = value[:middle]
        if len(encode_bounded()) <= limit:
            low = middle
        else:
            high = middle - 1
    bounded["truncated_context"] = value[:low]
    return encode_bounded()


def _bound_items(items):
    bounded = []
    remaining = settings.CRETE_AI_CONTEXT_TOTAL_CHARS
    for item in items[: settings.CRETE_AI_CONTEXT_ITEM_LIMIT]:
        if remaining <= 0:
            break
        item = dict(item)
        item["title"] = _truncate(item["title"], remaining)
        content = _bound_context_content(item["content"], remaining - len(item["title"]))
        if content is None:
            break
        item["content"] = content
        remaining -= len(item["title"])
        remaining -= len(item["content"])
        bounded.append(item)
    return bounded


def sanitize_thread_citations(thread, workspace, user, allowed_project_ids, restricted_project_ids):
    citation_ids = set()
    for message in thread.get("messages", []):
        citations = message.get("citations")
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict) or citation.get("object_type") != "issue":
                continue
            try:
                citation_ids.add(UUID(str(citation.get("object_id"))))
            except (TypeError, ValueError):
                continue

    visible_issues = (
        Issue.issue_objects.select_related("project")
        .filter(
            id__in=citation_ids,
            workspace=workspace,
            project_id__in=allowed_project_ids,
        )
        .filter(_visible_issue_filter(allowed_project_ids, restricted_project_ids, user))
    )
    issues_by_id = {str(issue.id): issue for issue in visible_issues}

    result = dict(thread)
    result["messages"] = []
    for message in thread.get("messages", []):
        sanitized_message = dict(message)
        sanitized_message["citations"] = []
        seen = set()
        citations = message.get("citations")
        if not isinstance(citations, list):
            result["messages"].append(sanitized_message)
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            citation_id = citation.get("citation_id")
            issue = issues_by_id.get(str(citation.get("object_id")))
            if (
                not isinstance(citation_id, int)
                or isinstance(citation_id, bool)
                or not 1 <= citation_id <= _MAX_CITATION_ID
                or issue is None
                or citation_id in seen
            ):
                continue
            seen.add(citation_id)
            sanitized_message["citations"].append(
                {
                    "citation_id": citation_id,
                    "object_type": "issue",
                    "object_id": str(issue.id),
                    "project_id": str(issue.project_id),
                    "project_identifier": issue.project.identifier,
                    "sequence_id": issue.sequence_id,
                    "title": _truncate(issue.name, settings.CRETE_AI_CONTEXT_ITEM_CHARS),
                }
            )
        result["messages"].append(sanitized_message)
    return result


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
