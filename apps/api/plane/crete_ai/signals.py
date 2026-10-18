import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from plane.crete_ai.tasks import delete_issue_index, sync_issue_index
from plane.db.models import Issue


logger = logging.getLogger("plane.api")


def _enabled():
    return bool(settings.CRETE_AI_SERVICE_URL and settings.CRETE_AI_SHARED_SECRET)


def _enqueue(task, *args):
    try:
        task.delay(*args)
    except Exception:
        logger.exception("Unable to enqueue Crete AI issue index synchronization")


def enqueue_issue_index_deletions(issue_refs):
    if not _enabled():
        return
    for issue_id, workspace_id in issue_refs:
        _enqueue(delete_issue_index, str(issue_id), str(workspace_id))


@receiver(post_save, sender=Issue, dispatch_uid="crete_ai_sync_issue")
def issue_saved(sender, instance, raw=False, **kwargs):
    if raw or not _enabled():
        return
    issue_id = str(instance.id)
    workspace_id = str(instance.workspace_id)
    transaction.on_commit(lambda: _enqueue(sync_issue_index, issue_id, workspace_id))


@receiver(post_delete, sender=Issue, dispatch_uid="crete_ai_delete_issue")
def issue_deleted(sender, instance, **kwargs):
    if not _enabled():
        return
    issue_id = str(instance.id)
    workspace_id = str(instance.workspace_id)
    transaction.on_commit(lambda: _enqueue(delete_issue_index, issue_id, workspace_id))
