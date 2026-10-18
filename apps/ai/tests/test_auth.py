# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import time
from types import SimpleNamespace

import pytest
from conftest import FakeRepository, signed_headers

from crete_plane_ai import auth


def test_verify_hmac_uses_constant_time_comparison(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(auth.hmac, "compare_digest", compare_digest)
    timestamp = str(int(time.time()))
    nonce = "00000000-0000-0000-0000-000000000001"
    signature = auth.signature_for("secret", timestamp, nonce, "POST", "/internal/chat", b"body")

    assert auth.verify_hmac(
        secret="secret",
        timestamp=timestamp,
        nonce=nonce,
        method="POST",
        target="/internal/chat",
        signature=signature,
        body=b"body",
        max_age_seconds=300,
    )
    assert calls == [(signature, signature)]


def test_internal_routes_reject_missing_and_stale_signatures(client, settings, repository: FakeRepository) -> None:
    url = f"/internal/workspaces/{repository.workspace_id}/threads?user_id={repository.user_id}"

    assert client.get(url).status_code == 401

    stale_timestamp = int(time.time()) - 301
    stale_response = client.get(
        url,
        headers=signed_headers(settings, "GET", url, timestamp=stale_timestamp),
    )
    assert stale_response.status_code == 401
    assert stale_response.json()["detail"] == "invalid or stale internal request signature"


def test_internal_route_accepts_valid_signature(client, settings, repository: FakeRepository) -> None:
    url = f"/internal/workspaces/{repository.workspace_id}/threads?user_id={repository.user_id}"
    response = client.get(url, headers=signed_headers(settings, "GET", url))

    assert response.status_code == 200
    assert response.json()["threads"][0]["id"] == str(repository.thread_id)


def test_signature_covers_target_and_nonce_cannot_be_replayed(client, settings, repository: FakeRepository) -> None:
    url = f"/internal/workspaces/{repository.workspace_id}/threads?user_id={repository.user_id}"
    headers = signed_headers(settings, "GET", url)

    assert client.get(url, headers=headers).status_code == 200
    assert client.get(url, headers=headers).status_code == 401

    changed_url = f"/internal/workspaces/{repository.workspace_id}/threads?user_id=00000000-0000-0000-0000-000000000001"
    assert client.get(changed_url, headers=signed_headers(settings, "GET", url)).status_code == 401


def test_signature_covers_exact_raw_body(client, settings) -> None:
    signed_body = b'{"object_id":"00000000-0000-0000-0000-000000000001"}'
    changed_body = b'{"object_id": "00000000-0000-0000-0000-000000000001"}'
    response = client.post(
        "/internal/index/delete",
        content=changed_body,
        headers={
            "Content-Type": "application/json",
            **signed_headers(settings, "POST", "/internal/index/delete", signed_body),
        },
    )

    assert response.status_code == 401


def test_internal_routes_reject_oversized_bodies(client, settings) -> None:
    body = (
        b'{"object_id":"00000000-0000-0000-0000-000000000001","padding":"'
        + (b"x" * settings.ai_max_request_bytes)
        + b'"}'
    )
    response = client.post(
        "/internal/index/delete",
        content=body,
        headers={
            "Content-Type": "application/json",
            **signed_headers(settings, "POST", "/internal/index/delete", body),
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "request body exceeds the size limit"


def test_non_ascii_timestamp_is_rejected_without_error() -> None:
    assert not auth.verify_hmac(
        secret="secret",
        timestamp="١٢٣",
        nonce="00000000-0000-0000-0000-000000000001",
        method="GET",
        target="/internal/threads",
        signature="0" * 64,
        body=b"",
        max_age_seconds=300,
        now=123,
    )


@pytest.mark.asyncio
async def test_body_limit_middleware_bounds_chunked_internal_requests(settings) -> None:
    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * settings.ai_max_request_bytes, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    async def consume_body(_scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = auth.InternalBodyLimitMiddleware(consume_body)
    scope = {
        "type": "http",
        "path": "/internal/chat",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }

    await middleware(scope, receive, send)

    assert sent[0]["status"] == 413
