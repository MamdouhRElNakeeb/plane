# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

from fastapi import Header, HTTPException, Request, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    pass


class InternalBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/internal/"):
            await self.app(scope, receive, send)
            return
        application = scope["app"]
        max_body_size = application.state.settings.ai_max_request_bytes
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._send_error(send, status.HTTP_400_BAD_REQUEST, "invalid content-length header")
                return
            if declared_size < 0:
                await self._send_error(send, status.HTTP_400_BAD_REQUEST, "invalid content-length header")
                return
            if declared_size > max_body_size:
                await self._send_error(
                    send,
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "request body exceeds the size limit",
                )
                return

        received_size = 0

        async def limited_receive() -> Message:
            nonlocal received_size
            message = await receive()
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > max_body_size:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._send_error(
                send,
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "request body exceeds the size limit",
            )

    @staticmethod
    async def _send_error(send: Send, status_code: int, detail: str) -> None:
        body = f'{{"detail":"{detail}"}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def canonical_target(request: Request) -> str:
    query = urlencode(sorted(parse_qsl(request.url.query, keep_blank_values=True)))
    return f"{request.url.path}?{query}" if query else request.url.path


def signature_for(secret: str, timestamp: str, nonce: str, method: str, target: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    payload = "\n".join([timestamp, nonce, method.upper(), target, body_hash]).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_hmac(
    *,
    secret: str,
    timestamp: str,
    nonce: str,
    method: str,
    target: str,
    signature: str,
    body: bytes,
    max_age_seconds: int,
    now: float | None = None,
) -> bool:
    try:
        request_time = int(timestamp)
        timestamp.encode("ascii")
        UUID(nonce)
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - request_time) > max_age_seconds:
        return False
    expected = signature_for(secret, timestamp, nonce, method, target, body)
    return hmac.compare_digest(expected, signature.lower())


async def require_internal_auth(
    request: Request,
    x_crete_ai_timestamp: str | None = Header(default=None),
    x_crete_ai_nonce: str | None = Header(default=None),
    x_crete_ai_signature: str | None = Header(default=None),
) -> None:
    if not x_crete_ai_timestamp or not x_crete_ai_nonce or not x_crete_ai_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing internal authentication headers",
        )
    settings = request.app.state.settings
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > settings.ai_max_request_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="request body exceeds the size limit",
                )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid content-length header",
            ) from error
    body = await request.body()
    if len(body) > settings.ai_max_request_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="request body exceeds the size limit",
        )
    if not verify_hmac(
        secret=settings.crete_ai_shared_secret.get_secret_value(),
        timestamp=x_crete_ai_timestamp,
        nonce=x_crete_ai_nonce,
        method=request.method,
        target=canonical_target(request),
        signature=x_crete_ai_signature,
        body=body,
        max_age_seconds=settings.ai_hmac_max_age_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or stale internal request signature",
        )
    claimed = await request.app.state.repository.claim_request_nonce(
        UUID(x_crete_ai_nonce),
        int(x_crete_ai_timestamp) + settings.ai_hmac_max_age_seconds,
    )
    if not claimed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="internal request nonce was already used",
        )
