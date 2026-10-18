import hashlib
import hmac
import json
import time
from urllib.parse import quote
from urllib.parse import urlencode
from uuid import uuid4

import requests
from django.conf import settings


class AIServiceError(Exception):
    """An error communicating with the internal AI service."""


def _configuration():
    base_url = settings.CRETE_AI_SERVICE_URL.rstrip("/")
    secret = settings.CRETE_AI_SHARED_SECRET
    if not base_url or not secret:
        raise AIServiceError("The AI service is not configured")
    return base_url, secret


def encode_json(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _canonical_target(path, params=None):
    if not params:
        return path
    query = urlencode(sorted((str(key), str(value)) for key, value in params.items()))
    return f"{path}?{query}"


def build_hmac_headers(method, target, raw_body, timestamp=None, nonce=None):
    _, secret = _configuration()
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    nonce = nonce or uuid4().hex
    body_hash = hashlib.sha256(raw_body).hexdigest()
    message = "\n".join([timestamp, nonce, method.upper(), target, body_hash]).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Crete-AI-Timestamp": timestamp,
        "X-Crete-AI-Nonce": nonce,
        "X-Crete-AI-Signature": signature,
    }


class CreteAIClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def request(self, method, path, payload=None, params=None, stream=False):
        base_url, _ = _configuration()
        raw_body = encode_json(payload) if payload is not None else b""
        headers = build_hmac_headers(method, _canonical_target(path, params), raw_body)
        try:
            response = self.session.request(
                method,
                f"{base_url}{path}",
                params=params,
                data=raw_body or None,
                headers=headers,
                timeout=(settings.CRETE_AI_CONNECT_TIMEOUT, settings.CRETE_AI_READ_TIMEOUT),
                stream=stream,
            )
        except requests.RequestException as exc:
            raise AIServiceError("The AI service is unavailable") from exc
        if not 200 <= response.status_code < 300:
            response.close()
            raise AIServiceError("The AI service rejected the request")
        return response

    def request_json(self, method, path, payload=None, params=None):
        response = self.request(method, path, payload=payload, params=params)
        try:
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise AIServiceError("The AI service returned an invalid response") from exc
        finally:
            response.close()

    def list_threads(self, workspace_id, user_id):
        return self.request_json(
            "GET",
            f"/internal/workspaces/{quote(str(workspace_id))}/threads",
            params={"user_id": str(user_id)},
        )

    def create_thread(self, workspace_id, user_id, payload):
        return self.request_json(
            "POST",
            f"/internal/workspaces/{quote(str(workspace_id))}/threads",
            payload={"user_id": str(user_id), **payload},
        )

    def get_thread(self, workspace_id, thread_id, user_id):
        return self.request_json(
            "GET",
            f"/internal/workspaces/{quote(str(workspace_id))}/threads/{quote(str(thread_id))}",
            params={"user_id": str(user_id)},
        )

    def delete_thread(self, workspace_id, thread_id, user_id):
        return self.request_json(
            "DELETE",
            f"/internal/workspaces/{quote(str(workspace_id))}/threads/{quote(str(thread_id))}",
            params={"user_id": str(user_id)},
        )

    def retrieve(self, payload):
        return self.request_json("POST", "/internal/retrieve", payload=payload)

    def plan_report(self, payload):
        return self.request_json("POST", "/internal/report-plan", payload=payload)

    def stream_chat(self, payload):
        return self.request("POST", "/internal/chat", payload=payload, stream=True)

    def get_action(self, action_id, workspace_id, user_id):
        return self.request_json(
            "GET",
            f"/internal/actions/{quote(str(action_id))}",
            params={"workspace_id": str(workspace_id), "user_id": str(user_id)},
        )

    def complete_action(self, action_id, workspace_id, user_id, result):
        return self.request_json(
            "POST",
            f"/internal/actions/{quote(str(action_id))}/complete",
            payload={
                "workspace_id": str(workspace_id),
                "user_id": str(user_id),
                "result": result,
            },
        )

    def index_issue(self, payload):
        return self.request_json("POST", "/internal/index", payload=payload)

    def delete_index(self, object_id, workspace_id):
        return self.request_json(
            "POST",
            "/internal/index/delete",
            payload={
                "object_type": "issue",
                "object_id": str(object_id),
                "workspace_id": str(workspace_id),
            },
        )
