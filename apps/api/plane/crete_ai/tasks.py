from celery import shared_task

from plane.crete_ai.client import AIServiceError, CreteAIClient
from plane.crete_ai.context import issue_index_payload
from plane.db.models import Issue


def synchronize_issue_index(issue_id, workspace_id):
    issue = Issue.issue_objects.select_related("project", "state").filter(pk=issue_id).first()
    client = CreteAIClient()
    if issue is None:
        return client.delete_index(issue_id, workspace_id)
    return client.index_issue(issue_index_payload(issue))


@shared_task(
    bind=True,
    autoretry_for=(AIServiceError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_issue_index(self, issue_id, workspace_id):
    return synchronize_issue_index(issue_id, workspace_id)


@shared_task(
    bind=True,
    autoretry_for=(AIServiceError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def delete_issue_index(self, issue_id, workspace_id):
    return CreteAIClient().delete_index(issue_id, workspace_id)
