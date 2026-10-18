from django.apps import AppConfig


class CreteAIConfig(AppConfig):
    name = "plane.crete_ai"
    verbose_name = "Crete AI"

    def ready(self):
        from . import signals  # noqa: F401
