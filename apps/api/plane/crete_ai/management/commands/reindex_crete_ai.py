from django.core.management.base import BaseCommand, CommandError

from plane.crete_ai.client import AIServiceError, CreteAIClient
from plane.crete_ai.context import issue_index_payload
from plane.db.models import Issue


class Command(BaseCommand):
    help = "Reindex all active, non-draft work items in the Crete AI service"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > 2000:
            raise CommandError("--batch-size must be between 1 and 2000")

        queryset = Issue.issue_objects.select_related("project", "state").order_by("id")
        client = CreteAIClient()
        indexed = 0
        try:
            for issue in queryset.iterator(chunk_size=batch_size):
                client.index_issue(issue_index_payload(issue))
                indexed += 1
                if indexed % batch_size == 0:
                    self.stdout.write(f"Indexed {indexed} work items")
        except AIServiceError as exc:
            raise CommandError(f"Reindex stopped after {indexed} work items: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Indexed {indexed} work items"))
