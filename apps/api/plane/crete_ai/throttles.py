from rest_framework.throttling import SimpleRateThrottle


class CreteAIUserThrottle(SimpleRateThrottle):
    scope = "crete_ai"

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        workspace_slug = view.kwargs.get("slug", "")
        ident = f"{request.user.id}:{workspace_slug}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class CreteAIChatThrottle(CreteAIUserThrottle):
    scope = "crete_ai_chat"
