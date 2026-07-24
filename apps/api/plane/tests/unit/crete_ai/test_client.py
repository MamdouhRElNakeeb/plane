import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from plane.crete_ai.client import CreteAIClient, build_hmac_headers, encode_json


pytestmark = pytest.mark.unit


@override_settings(
    CRETE_AI_SERVICE_URL="http://crete-ai",
    CRETE_AI_SHARED_SECRET="shared-secret",
    CRETE_AI_CONNECT_TIMEOUT=2,
    CRETE_AI_READ_TIMEOUT=30,
)
def test_hmac_signs_timestamp_and_exact_raw_body():
    body = encode_json({"prompt": "hello", "count": 2})
    nonce = "00000000000000000000000000000001"
    headers = build_hmac_headers(
        "POST",
        "/internal/chat",
        body,
        timestamp=1700000000,
        nonce=nonce,
    )

    body_hash = hashlib.sha256(body).hexdigest()
    expected = hmac.new(
        b"shared-secret",
        f"1700000000\n{nonce}\nPOST\n/internal/chat\n{body_hash}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Crete-AI-Timestamp"] == "1700000000"
    assert headers["X-Crete-AI-Nonce"] == nonce
    assert headers["X-Crete-AI-Signature"] == expected


@override_settings(
    CRETE_AI_SERVICE_URL="http://crete-ai/",
    CRETE_AI_SHARED_SECRET="shared-secret",
    CRETE_AI_CONNECT_TIMEOUT=2,
    CRETE_AI_READ_TIMEOUT=30,
)
def test_client_sends_signed_raw_body_without_session_credentials():
    response = MagicMock(status_code=200, content=b'{"ok":true}')
    response.json.return_value = {"ok": True}
    session = MagicMock()
    session.request.return_value = response

    payload = {"workspace_id": "workspace", "query": "hello"}
    result = CreteAIClient(session=session).request_json("POST", "/internal/retrieve", payload=payload)

    assert result == {"ok": True}
    kwargs = session.request.call_args.kwargs
    assert kwargs["data"] == encode_json(payload)
    assert "Cookie" not in kwargs["headers"]
    assert "Authorization" not in kwargs["headers"]
    response.close.assert_called_once()


@override_settings(
    CRETE_AI_SERVICE_URL="http://crete-ai",
    CRETE_AI_SHARED_SECRET="shared-secret",
    CRETE_AI_CONNECT_TIMEOUT=2,
    CRETE_AI_READ_TIMEOUT=30,
)
def test_index_deletion_includes_tenant_scope():
    response = MagicMock(status_code=200, content=b'{"deleted":true}')
    response.json.return_value = {"deleted": True}
    session = MagicMock()
    session.request.return_value = response

    CreteAIClient(session=session).delete_index("issue-id", "workspace-id")

    payload = json.loads(session.request.call_args.kwargs["data"])
    assert payload == {
        "object_type": "issue",
        "object_id": "issue-id",
        "workspace_id": "workspace-id",
    }


@override_settings(
    CRETE_AI_SERVICE_URL="http://crete-ai",
    CRETE_AI_SHARED_SECRET="shared-secret",
    CRETE_AI_CONNECT_TIMEOUT=2,
    CRETE_AI_READ_TIMEOUT=30,
)
def test_report_plan_uses_signed_internal_endpoint():
    response = MagicMock(status_code=200, content=b'{"mode":"report"}')
    response.json.return_value = {"mode": "report"}
    session = MagicMock()
    session.request.return_value = response
    payload = {"prompt": "Count work by status.", "catalog": []}

    result = CreteAIClient(session=session).plan_report(payload)

    assert result == {"mode": "report"}
    assert session.request.call_args.args[:2] == ("POST", "http://crete-ai/internal/report-plan")
    assert session.request.call_args.kwargs["data"] == encode_json(payload)
